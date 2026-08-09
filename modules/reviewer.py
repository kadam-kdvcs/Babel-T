# -*- coding: utf-8 -*-
"""审校报告模块（阶段 3：可配置双模式）。

模块作用
--------
生成审校报告文本，支持两种模式（由环境变量 REVIEW_ENGINE 决定，
见 load_review_config）：

- mock 模式（默认）：不联网，生成与阶段 1 逐字节一致的占位报告，
  用于本地演示与测试；
- api 模式：读取 prompts/review_report_prompt.md 提示词模板，把
  段落/译文/术语与专名命中填入用户消息，调用 DeepSeek（或任意
  OpenAI 兼容接口）生成 8 项结构的 markdown 审校报告。
  翻译 API 无法接入术语表（阶段 2 已知约束），术语一致性必须由
  LLM 在审校报告中把关，因此 LLM 报告以真实翻译结果为审校依据。

本模块是纯函数模块（无 UI、无全局可变状态）。配置的定义与加载
（环境变量名、默认值、ReviewConfig、load_review_config）集中在
modules/settings.py——本模块只通过参数接收配置，不读取环境变量，
保证可测试性。模块内不 import python-dotenv：.env 的加载只发生在
app.py 顶部，命令行直接调用 run_pipeline 时需要自行 load_dotenv。

失败策略（与翻译模块对称）
--------------------------
- api 模式缺密钥（has_credentials 为 False）→ 不报错，回退占位报告，
  由页面显示黄色提示；
- 调用失败（超时/连接失败/HTTP 非 200/响应解析失败）→ 抛本模块的
  ReviewError 家族异常，由 app.py 转 st.error，旧结果保留。

安全约定
--------
- 绝不把 API 密钥写入代码、日志、错误消息；
- 异常消息全部为中文，业务错误只带响应文本前 200 字符；
- 密钥只允许存在于环境变量（.env）中，.env 已被 .gitignore 忽略。
"""

from pathlib import Path  # 标准库：跨平台路径处理（定位提示词模板）

import requests  # 第三方库：HTTP 客户端，用于调用 LLM 审校接口

# 配置集中管理（阶段 3）：环境变量名/默认值/ReviewConfig/
# load_review_config 全部定义在 modules/settings.py，本模块只按需导入
from modules.settings import (
    MOCK_ENGINE,             # 引擎标识：mock（占位，默认）
    ReviewConfig,            # 配置对象（参数类型标注用）
    load_review_config,      # 加载配置（审校入口处调用）
)


# ---------------------------------------------------------------------------
# 模块级私有常量
# ---------------------------------------------------------------------------

# 提示词模板路径：项目根目录 / prompts / review_report_prompt.md
# （本文件所在目录的上一级即项目根，不依赖启动时的当前目录）
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "review_report_prompt.md"

# 模板里可替换的 4 个占位符名（写进模板时用花括号包住，如 {source_paragraphs}）
_PLACEHOLDERS = ("source_paragraphs", "translations", "term_hits", "name_hits")

# 模板分隔标记：程序按用户标记拆出用户消息段（占位符所在），
# 按系统标记截取系统消息段（去掉文件头部的维护者说明）。
# 校验要求两个标记都存在、且系统标记在用户标记之前。
_USER_SECTION_MARKER = "## 用户消息"
_SYSTEM_SECTION_MARKER = "## 系统消息"

# LLM 采样温度：固定 0.3（偏低温，审校任务希望输出稳定、少发散；
# 阶段 3 决策：不配环境变量）
_TEMPERATURE = 0.3


# ---------------------------------------------------------------------------
# 异常体系（全部中文消息，绝不包含任何密钥）
# ---------------------------------------------------------------------------

class ReviewError(Exception):
    """审校异常基类（阶段 3 起，一切审校失败都抛此体系下的异常）。

    作用：让调用方（app.py）用一条 except 捕获全部审校失败，
          统一转成页面错误提示，而不是让页面整体崩溃。
    输入：message —— 中文错误消息（不含密钥）。
    输出：异常实例。
    """


class ReviewNetworkError(ReviewError):
    """网络层失败：超时 / 连接失败。

    作用：区分「网络没通」与「接口返回了业务错误」两类失败，
          方便排查是网络问题还是服务问题。
    输入：message —— 中文错误消息。
    输出：异常实例。
    """


class ReviewBusinessError(ReviewError):
    """业务层失败：HTTP 状态码非 200。

    作用：携带状态码与响应文本片段（前 200 字符），便于排查
          是凭证问题还是服务问题。
    输入：message —— 中文错误消息；status_code —— HTTP 状态码；
          snippet —— 响应文本前 200 字符的截断片段。
    输出：异常实例，携带 status_code / snippet 属性。
    """

    def __init__(self, message: str, status_code: int | None = None,
                 snippet: str | None = None):
        self.status_code = status_code
        self.snippet = snippet
        super().__init__(message)


class ReviewParseError(ReviewError):
    """解析层失败：响应不是合法 JSON，或缺少 choices / message.content。

    作用：区分「响应格式不对」与「接口明确报错」，前者通常是
          接口协议变动，需要开发排查。
    输入：message —— 中文错误消息。
    输出：异常实例。
    """


# ---------------------------------------------------------------------------
# 审校入口（双模式编排）
# ---------------------------------------------------------------------------

def generate_review_report(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict],
    name_hits: list[dict],
) -> str:
    """生成审校报告文本（markdown 格式，双模式入口）。

    作用：审校流水线的出口。按配置决定走 mock（占位报告）还是 api
          （真实 LLM 审校报告）：
          - 空输入（paragraphs 为空）→ 直接返回占位报告，api 模式
            同样不发任何请求（与翻译契约对称）；
          - engine == "mock"，或 engine == "api" 但缺凭证
            （has_credentials 为 False）→ 走 _generate_mock_report，
            占位文案与阶段 1 逐字节一致；
          - engine == "api" 且有凭证 → 读取提示词模板、填入数据、
            调用 LLM，返回 8 项结构的 markdown 审校报告。
    输入：paragraphs —— list[str]，阿语段落列表；
          translations —— list[str]，与段落一一对应的译文列表；
          term_hits —— list[dict]，术语命中列表（scan_glossary 的输出，
          含「阿语原文」「处理方式」等键与 "paragraphs"/"count"）；
          name_hits —— list[dict]，专名命中列表，结构同 term_hits
          （无「领域」「处理方式」两列）。
    输出：str —— 审校报告文本（markdown 格式，可在 Streamlit 中渲染）；
          各列表为空时返回仅含统计数字（0 段、0 条）的占位报告，不报错。
    异常：api 模式任一阶段失败（网络/业务/解析）抛对应 ReviewError
          子类；模板缺失抛 FileNotFoundError；环境变量非法抛 ValueError。
    """
    # 空输入短路：不加载配置、不发请求（契约：api 模式也如此）
    if not paragraphs:
        return _generate_mock_report(paragraphs, term_hits, name_hits)

    # 加载审校配置（读取环境变量；非法值在此抛 ValueError，由 UI 层提示）
    config = load_review_config()

    # 回退条件：mock 模式本来就不联网；api 模式缺密钥也回退占位报告
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return _generate_mock_report(paragraphs, term_hits, name_hits)

    # api 模式：编排「模板 → 数据填充 → 请求 → 解析」全流程
    return _review_with_api(paragraphs, translations, term_hits, name_hits, config)


def _generate_mock_report(
    paragraphs: list[str], term_hits: list[dict], name_hits: list[dict]
) -> str:
    """生成占位审校报告（mock 模式 / 缺密钥回退；不联网）。

    作用：文案与阶段 1 逐字节一致，保证阶段 1 的旧测试断言零改动。
    输入：paragraphs —— 阿语段落列表；term_hits / name_hits ——
          术语/专名命中列表。
    输出：str —— 占位报告文本（含统计数字）。
    """
    # 统计口径：条数 = 去重后的词条数（len），次数 = 全部命中累计
    # （count 求和；hit.get("count", 1) 兼容无 count 字段的旧结构）
    term_total = sum(hit.get("count", 1) for hit in term_hits)
    name_total = sum(hit.get("count", 1) for hit in name_hits)
    return (
        "（占位）审校报告：待接入 LLM 后生成。\n\n"
        f"本次共 {len(paragraphs)} 段，术语命中 {len(term_hits)} 条（共 {term_total} 次），"
        f"专名命中 {len(name_hits)} 条（共 {name_total} 次）。"
    )


# ---------------------------------------------------------------------------
# 提示词模板：加载与数据填充
# ---------------------------------------------------------------------------

def _load_prompt_template() -> str:
    """读取提示词模板并做结构校验。

    作用：读取 prompts/review_report_prompt.md（UTF-8）并校验结构：
          - 「## 系统消息」与「## 用户消息」两个分隔标记都必须存在
            （错误消息点名缺哪个），且系统标记在用户标记之前；
          - 4 个占位符必须全部位于「用户消息段」内且各恰好出现 1 次。
            注意：校验对象是「按用户标记拆出的用户段」而不是全模板——
            若占位符被误挪进系统消息段（或标记位置异常），全模板计数
            仍可能恰好为 1，但实际发给 LLM 的用户消息会残留字面
            占位符或为空。
    输入：无（读 _PROMPT_PATH 指向的文件）。
    输出：str —— 模板全文。
    异常：文件缺失抛 FileNotFoundError（app.py 已捕获，提示数据文件
          缺失）；结构非法抛 ValueError（中文消息，点名缺失的标记）。
    """
    # 显式 UTF-8 读取（Windows 默认 GBK，模板是中文内容必须显式编码）
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    # 两个分隔标记都必须存在：_render_user_message / _review_with_api
    # 靠它们拆段，任一缺失即模板非法（错误消息点名缺哪个标记）
    if _SYSTEM_SECTION_MARKER not in template:
        raise ValueError(f"提示词模板缺少「{_SYSTEM_SECTION_MARKER}」分隔标记")
    if _USER_SECTION_MARKER not in template:
        raise ValueError(f"提示词模板缺少「{_USER_SECTION_MARKER}」分隔标记")

    # 标记顺序：str.find(sub) 返回子串首次出现的下标；
    # 系统标记必须在用户标记之前，否则拆出的「系统段」会混进用户数据
    if template.find(_SYSTEM_SECTION_MARKER) > template.find(_USER_SECTION_MARKER):
        raise ValueError("提示词模板中「## 系统消息」必须在「## 用户消息」之前")

    # 占位符校验：只针对用户消息段（用户标记之后的内容）逐个数数；
    # str.count(sub) 统计子串出现次数，每个占位符必须恰好 1 次
    # （标记存在已在上方校验，split(…, 1)[1] 必然存在）
    user_section = template.split(_USER_SECTION_MARKER, 1)[1]
    for name in _PLACEHOLDERS:
        if user_section.count(f"{{{name}}}") != 1:
            raise ValueError(
                f"提示词模板用户消息段中占位符 {name!r} 必须恰好出现 1 次"
            )
    return template


def _format_hits_text(
    term_hits: list[dict] | None, name_hits: list[dict] | None
) -> str:
    """把术语/专名命中列表统一格式化为清单文本。

    作用：把 glossary.scan_glossary 的命中结果整理成一段人读的命中清单，
          填入提示词模板的用户消息，作为 LLM 审校的术语/专名依据。
          术语与专名两库表头不同（六列 vs 四列），这里用 .get 兼容键差异
          （专名无「领域」「处理方式」两列，不输出，不会 KeyError）。
    输入：term_hits —— 术语命中列表（每条含「阿语原文」「中文译文」
          「类别」「领域」「备注」「处理方式」与 "paragraphs" 段落号
          列表、"count" 次数）；name_hits —— 专名命中列表（结构同上，
          但无「领域」「处理方式」两列）。可为 None。
    输出：str —— 命中清单文本。格式：
              【术语命中】共 N 条
              - 阿语原文「…」→ 中文译文「…」（类别：…；领域：…；
                备注：…；处理方式：…；出现段落：1, 4, 5；出现次数：7）
              【专名命中】共 M 条
              - …
          某类无命中输出「【术语命中】无」；处理方式保留 CSV 原始值
          （force_check 等），不做 UI 显示映射（映射是展示层的事）。
    """
    # 术语六列表头中「阿语原文/中文译文」单独放在行首，其余四列进括号；
    # 专名四列表头中只有「类别」「备注」两列进括号
    term_extra_keys = ("类别", "领域", "备注", "处理方式")
    name_extra_keys = ("类别", "备注")

    def _format_group(hits: list[dict], kind: str, extra_keys: tuple) -> str:
        # 无命中输出「无」（空列表与 None 都在调用处归一化为 []）
        if not hits:
            return f"【{kind}命中】无"
        lines = [f"【{kind}命中】共 {len(hits)} 条"]
        for hit in hits:
            # 括号内各列用「；」分隔；.get(key, "") 缺列补空串（脏数据兜底，
            # 专名缺「领域」「处理方式」也不会 KeyError）
            details = "；".join(f"{key}：{hit.get(key, '')}" for key in extra_keys)
            # 出现段落列表（如 [1, 4, 5]）转成 "1, 4, 5" 字符串；
            # 出现次数缺 count 字段时按 1 次计（与占位报告统计口径一致）
            paragraphs = ", ".join(str(p) for p in hit.get("paragraphs", []))
            count = hit.get("count", 1)
            lines.append(
                f"- 阿语原文「{hit.get('阿语原文', '')}」→ "
                f"中文译文「{hit.get('中文译文', '')}」"
                f"（{details}；出现段落：{paragraphs}；出现次数：{count}）"
            )
        return "\n".join(lines)

    # 术语节 + 专名节，两节之间用换行连接
    term_section = _format_group(term_hits or [], "术语", term_extra_keys)
    name_section = _format_group(name_hits or [], "专名", name_extra_keys)
    return f"{term_section}\n{name_section}"


def _format_numbered(items: list[str], label: str) -> str:
    """把列表逐项编号化输出，让 LLM 能按段号引用。

    作用：把段落/译文列表转成「第N段：…」/「第N段译文：…」的多行文本，
          段号 1 基编号。审校报告的第 4 项要求「段号 + 原文引文 + 译文」
          引用，编号后 LLM 才能精确指认某个段落。
    输入：items —— 字符串列表；label —— 编号后的单位名称（如「段」）。
    输出：str —— 每项一行、以「第N+label：」开头的多行文本；
          空列表返回空串。
    """
    # 列表推导式：enumerate 从 1 开始编号，拼出「第N段：文本」一行
    return "\n".join(f"第{i}{label}：{item}" for i, item in enumerate(items, start=1))


def _render_user_message(
    template: str,
    *,
    source_paragraphs: str,
    translations: str,
    term_hits: str,
    name_hits: str,
) -> str:
    """把提示词模板的用户消息段中的 4 个占位符替换为真实数据。

    作用：模板以「## 用户消息」为界——前半是系统消息（不替换），
          后半是用户消息（含 4 个占位符）。本函数只对后半做替换，
          返回替换后的用户消息文本。
    输入：template —— 模板全文；source_paragraphs / translations ——
          编号化后的段落/译文文本；term_hits / name_hits —— 格式化后的
          术语/专名命中清单文本。
    输出：str —— 替换完成后的用户消息文本。
    异常：模板中找不到「## 用户消息」标记时抛 ValueError。
    """
    # str.split(marker)：按标记把模板切成多段；标记之后的第一段即用户消息
    parts = template.split(_USER_SECTION_MARKER)
    if len(parts) < 2:
        raise ValueError("提示词模板缺少「## 用户消息」标记")
    user_section = parts[1]

    # 4 个占位符逐一做纯文本替换。刻意用 str.replace 而不用 format：
    # 模板正文里可能含有中文括号「（」「）」等字符，format 语法会把
    # 花括号内容当作格式字段解析（如 {（} 会直接报错），replace 是
    # 逐字节的纯文本替换，对模板内容零约束。
    for name, text in (
        ("source_paragraphs", source_paragraphs),
        ("translations", translations),
        ("term_hits", term_hits),
        ("name_hits", name_hits),
    ):
        # f"{{{name}}}" 生成 "{name}" 字面量（花括号本身也是替换目标）
        user_section = user_section.replace(f"{{{name}}}", text)
    return user_section


# ---------------------------------------------------------------------------
# 请求构造与发送
# ---------------------------------------------------------------------------

def _build_api_url(config: ReviewConfig) -> str:
    """构造 LLM 接口地址（base URL + /chat/completions）。

    作用：DeepSeek 与 OpenAI 兼容接口的补全路径都是 /chat/completions；
          base URL 可能被用户填成带尾斜杠的写法，先 rstrip("/") 去掉，
          再拼接，避免出现 // 双斜杠。
    输入：config —— 审校配置（提供 api_url）。
    输出：str —— 形如 https://api.deepseek.com/chat/completions。
    """
    # str.rstrip("/")：去掉字符串末尾的全部斜杠（只去尾，不影响中间的）
    return config.api_url.rstrip("/") + "/chat/completions"


def _build_request_payload(
    config: ReviewConfig, system: str, user: str
) -> dict:
    """构造一次 LLM 审校请求的 JSON 请求体。

    作用：按 OpenAI 兼容接口（DeepSeek 同款）组装请求体——模型名、
          system/user 双角色消息与固定 temperature。请求体不包含任何
          密钥：密钥只出现在 HTTP 头 Authorization 里（见
          _review_with_api）。
    输入：config —— 审校配置（提供 model）；system —— 系统消息文本；
          user —— 用户消息文本（占位符已替换）。
    输出：dict —— 请求体，直接作为 requests.post(json=...) 的参数。
    """
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": _TEMPERATURE,
    }


def _review_with_api(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict],
    name_hits: list[dict],
    config: ReviewConfig,
) -> str:
    """执行一次完整的 LLM 审校请求（api 模式核心编排）。

    作用：按「读模板 → 拆系统/用户段 → 格式化数据 → 替换占位符 →
          构造请求 → POST → 解析响应」的顺序完成一次审校调用。
    输入：paragraphs / translations / term_hits / name_hits —— 审校数据；
          config —— 审校配置（提供 api_key / api_url / model / timeout）。
    输出：str —— LLM 生成的审校报告文本（markdown）。
    异常：超时/连接失败 → ReviewNetworkError；HTTP 非 200 →
          ReviewBusinessError（附状态码与响应片段）；解析失败 →
          ReviewParseError；以上消息全部中文且不含密钥。
    """
    # 1. 读取提示词模板（缺失 → FileNotFoundError；结构非法 → ValueError）
    template = _load_prompt_template()

    # 2. 拆系统消息段：先按用户标记切掉「用户消息」部分，再在剩余部分
    #    按系统标记截到标记之后——文件头部（标题行 + 维护者说明
    #    blockquote）在系统标记之前，是给人看的说明文字，不应随系统
    #    消息发给 LLM（提示词噪音）；保留「## 系统消息」标题本身无妨，
    #    LLM 能理解这是系统消息段的标题。
    #    （两个标记的存在与先后顺序已由 _load_prompt_template 校验）
    system_part = template.split(_USER_SECTION_MARKER)[0]
    system_message = system_part.split(_SYSTEM_SECTION_MARKER, 1)[1].strip()

    # 3. 数据格式化：段落/译文编号化（LLM 引用段号用）；
    #    术语与专名命中统一清单（两节合并为一段文本，同时填入模板里
    #    相邻的两个命中占位符）
    hits_text = _format_hits_text(term_hits, name_hits)
    user_message = _render_user_message(
        template,
        source_paragraphs=_format_numbered(paragraphs, "段"),
        translations=_format_numbered(translations, "段译文"),
        term_hits=hits_text,
        name_hits=hits_text,
    )

    # 4. 构造请求：URL（base + /chat/completions）与请求体
    url = _build_api_url(config)
    payload = _build_request_payload(config, system_message, user_message)

    # 5. 发送请求。密钥只放在 Authorization 请求头里（Bearer 前缀是
    #    OpenAI 兼容接口的通行认证写法），绝不进请求体；
    #    timeout 来自配置（防请求挂死）
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
        )
    except requests.exceptions.Timeout as e:
        # 超时：requests 把读超时/连超时都归入 Timeout 异常
        raise ReviewNetworkError(
            f"审校请求超时（{config.timeout_seconds} 秒）"
        ) from e
    except requests.exceptions.RequestException as e:
        # 连接失败（DNS/拒连/断线等）：错误消息只带原因，绝不含密钥
        raise ReviewNetworkError(f"审校请求连接失败：{e}") from e

    # 6. HTTP 状态码非 200 视为业务层失败：带状态码与响应文本片段
    #    （截断前 200 字符），便于排查；异常消息不含我们自己的密钥
    #    （密钥只在请求头里，不会出现在响应文本中）
    if response.status_code != 200:
        snippet = (response.text or "")[:200]
        raise ReviewBusinessError(
            f"审校接口返回 HTTP 状态码 {response.status_code}，响应片段：{snippet}",
            status_code=response.status_code,
            snippet=snippet,
        )

    # 7. 解析成功响应（200）：格式错误在 _parse_review_response 内转异常
    return _parse_review_response(response)


def _parse_review_response(response) -> str:
    """解析 LLM 审校接口的响应体。

    作用：把 OpenAI 兼容格式的响应逐层校验后取出报告文本：
          1) 非合法 JSON → ReviewParseError；
          2) 缺 choices 或 choices 不是列表 → ReviewParseError；
          3) 缺 message 或 message.content → ReviewParseError。
          成功响应形如 {"choices": [{"message": {"content": "报告"}}]}。
    输入：response —— requests 的响应对象（有 .json() 方法与 status_code）。
    输出：str —— LLM 生成的审校报告文本。
    """
    try:
        # response.json()：把响应体按 JSON 解析成 dict；
        # 响应体不是合法 JSON 时抛 ValueError（requests 的
        # JSONDecodeError 是它的子类）
        data = response.json()
    except ValueError as e:
        raise ReviewParseError("审校接口响应不是合法 JSON") from e

    # dict.get(key)：键不存在时返回 None，不抛 KeyError；
    # 逐层校验：choices → choices[0] → message → content
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ReviewParseError("审校接口响应缺少 choices 列表")
    first = choices[0]
    if not isinstance(first, dict):
        raise ReviewParseError("审校接口响应中 choices[0] 不是对象")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ReviewParseError("审校接口响应缺少 message 字段")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ReviewParseError("审校接口响应缺少 message.content 字段")
    return content
