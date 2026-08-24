# -*- coding: utf-8 -*-
"""翻译模块（阶段 2：可配置双模式）。

模块作用
--------
把阿语段落列表翻译为一一对应的译文列表，支持两种模式（由环境变量
TRANSLATION_ENGINE 决定，见 load_translation_config）：

- mock 模式（默认）：不联网，逐段生成占位译文
  「（占位译文·第N段）待接入翻译 API」，用于本地演示与测试；
- api 模式：调用阿里云机器翻译 TranslateGeneral 接口（requests 手写
  RPC 签名，不引入阿里云 SDK），逐段串行翻译、一次调用翻译一段。

本模块是纯函数模块（无 UI、无全局可变状态）。配置的定义与加载
（环境变量名、默认值、TranslationConfig、load_translation_config）
集中在 modules/settings.py——本模块只通过参数接收配置，不读取
环境变量，保证可测试性。
模块内不 import python-dotenv：.env 的加载只发生在 app.py 顶部，
命令行直接调用 run_pipeline 时需要自行 load_dotenv。

术语约束（阶段 2 决策 3）
------------------------
build_translation_constraints() 会正常生成约束文本，但**暂不发送**给
阿里云——已核实 TranslateGeneral 接口的业务参数仅含 Action/FormatType/
Scene/SourceLanguage/TargetLanguage/SourceText，没有 context/术语参数。
payload 构造函数中预留了参数位与注释，未来接入 LLM 审校时启用。

安全约定
--------
- 绝不把 AccessKey/Secret 写入代码、日志、错误消息；
- 异常消息全部为中文，只含段落号、错误码等非敏感信息；
- 密钥只允许存在于环境变量（.env）中，.env 已被 .gitignore 忽略。
"""

import base64  # 标准库：把二进制摘要转成文本（base64 编码）
import hashlib  # 标准库：摘要算法（sha1，供 hmac 使用）
import hmac  # 标准库：HMAC 消息认证（阿里云签名算法核心）
import uuid  # 标准库：生成请求唯一编号 SignatureNonce（防重放攻击）
import urllib.parse  # 标准库：URL 百分号编码（quote）
from collections.abc import Callable  # 标准库：类型标注用（回调函数）
from concurrent.futures import ThreadPoolExecutor, as_completed  # 标准库：并发翻译 + 动态回调
from datetime import datetime, timezone  # 标准库：UTC 时间戳（ISO8601）

import requests  # 第三方库：HTTP 客户端，用于调用阿里云翻译接口

# 配置集中管理（阶段 2 重构）：环境变量名/默认值/TranslationConfig/
# load_translation_config 全部定义在 modules/settings.py，本模块只按需导入
from modules.settings import (
    MOCK_ENGINE,             # 引擎标识：mock（占位，默认）
    TranslationConfig,       # 配置对象（参数类型标注用）
    load_translation_config, # 加载配置（翻译入口处调用）
)


# 并发翻译默认线程数：阿里云翻译一次只处理一段，多段同时请求可显著减少
# 总等待时间；默认 4 足够当前 5 段样例，过高可能触发接口限流。
_DEFAULT_MAX_WORKERS = 4


# ---------------------------------------------------------------------------
# 异常体系（全部中文消息，绝不包含任何密钥）
# ---------------------------------------------------------------------------

class TranslationError(Exception):
    """翻译异常基类（阶段 2 起，一切翻译失败都抛此体系下的异常）。

    作用：让调用方（app.py）用一条 except 捕获全部翻译失败，
          统一转成页面错误提示，而不是让页面整体崩溃。
    输入：message —— 中文错误消息（不含密钥）；
          paragraph_no —— 出错的段落号（1 基；批次外为 None）。
    输出：异常实例，携带 paragraph_no 属性供 UI 展示「第 N 段失败」。
    """

    def __init__(self, message: str, paragraph_no: int | None = None):
        self.paragraph_no = paragraph_no
        super().__init__(message)


class TranslationNetworkError(TranslationError):
    """网络层失败：连接失败 / 超时 / HTTP 状态码非 200。

    作用：区分「网络没通」与「接口返回了业务错误」两类失败，
          方便排查是网络问题还是凭证问题。
    输入：message —— 中文错误消息（含段落号）；paragraph_no —— 段落号。
    输出：异常实例。
    """


class TranslationBusinessError(TranslationError):
    """业务层失败：HTTP 200 但响应 Code ≠ "200"。

    典型场景：InvalidAccessKeyId（密钥错）、ServiceNotOpened（服务未开通）、
    SignatureDoesNotMatch（签名不匹配）。附阿里云错误码便于排查。
    输入：message —— 中文错误消息（含段落号与错误码）；
          paragraph_no —— 段落号；aliyun_code —— 阿里云返回的错误码。
    输出：异常实例，携带 aliyun_code 属性。
    """

    def __init__(self, message: str, paragraph_no: int | None = None,
                 aliyun_code: str | None = None):
        self.aliyun_code = aliyun_code
        super().__init__(message, paragraph_no)


class TranslationParseError(TranslationError):
    """解析层失败：响应不是合法 JSON，或缺少 Data.Translated 字段。

    作用：区分「响应格式不对」与「接口明确报错」，前者通常是
          接口协议变动，需要开发排查。
    输入：message —— 中文错误消息（含段落号）；paragraph_no —— 段落号。
    输出：异常实例。
    """


# ---------------------------------------------------------------------------
# 术语约束文本
# ---------------------------------------------------------------------------

def build_translation_constraints(
    term_hits: list[dict] | None,
    name_hits: list[dict] | None,
    paragraph_no: int | None = None,
) -> str:
    """把术语/专名命中列表构建为约束文本（阶段 2 暂不发送，未来接 LLM 用）。

    作用：把 glossary.scan_glossary 的命中结果整理成一段人读的约束说明，
          未来接入 LLM 审校时作为上下文传给模型。现在生成它，是为了
          阶段 3 无缝衔接，并保证「约束」这条数据流贯通可测。
    输入：term_hits / name_hits —— 命中列表（每条含「阿语原文」「中文译文」
          「类别」「处理方式」等字段与 "paragraphs" 段落号列表），可为 None；
          paragraph_no —— 段落号（1 基）。为 None 时取全部命中；
          为具体段号时只取出现在该段的命中（按 hit["paragraphs"] 过滤）。
    输出：str —— 约束文本。格式：
              【术语约束】
              - 阿语原文 → 中文译文（类别：X；处理方式：Y）
              【专名约束】
              - 阿语原文 → 中文译文（类别：X）
          无任何命中时返回 ""。处理方式取 CSV 原始值（force_check 等），
          不做 UI 显示映射（映射是展示层的事）。
    """
    # 命中过滤规则：paragraph_no 为 None 放行全部；否则要求段号出现在
    # hit.get("paragraphs", []) 里（缺该键的脏数据按空列表处理 → 不命中）。
    def _applies_to_paragraph(hit: dict) -> bool:
        if paragraph_no is None:
            return True
        return paragraph_no in hit.get("paragraphs", [])

    # 术语约束行：阿语原文 → 中文译文（类别：…；处理方式：…）
    # .get(key, "")：命中缺某列时补空串，避免 KeyError（脏数据兜底）
    term_lines = [
        f"- {hit.get('阿语原文', '')} → {hit.get('中文译文', '')}"
        f"（类别：{hit.get('类别', '')}；处理方式：{hit.get('处理方式', '')}）"
        for hit in (term_hits or [])
        if _applies_to_paragraph(hit)
    ]
    # 专名约束行：专名库无「处理方式」列（四列表头），只带类别
    name_lines = [
        f"- {hit.get('阿语原文', '')} → {hit.get('中文译文', '')}"
        f"（类别：{hit.get('类别', '')}）"
        for hit in (name_hits or [])
        if _applies_to_paragraph(hit)
    ]

    # 按「术语 → 专名」的顺序拼装，各自带章节标记；某类无命中就不输出该段
    parts = []
    if term_lines:
        parts.append("【术语约束】\n" + "\n".join(term_lines))
    if name_lines:
        parts.append("【专名约束】\n" + "\n".join(name_lines))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 翻译入口（双模式编排）
# ---------------------------------------------------------------------------

def translate_paragraphs(
    paragraphs: list[str],
    term_hits: list[dict] | None = None,
    name_hits: list[dict] | None = None,
) -> list[str]:
    """把阿语段落列表翻译为一一对应的译文列表（双模式入口）。

    作用：翻译流水线的唯一入口。按配置决定走 mock（占位）还是 api
          （真实翻译）：
          - engine == "mock"，或 engine == "api" 但缺凭证
            （has_credentials 为 False）→ 走 _translate_with_mock；
          - engine == "api" 且有凭证 → 逐段调用 _translate_with_api，
            每段先构建该段的术语约束（段落号为 i+1），串行翻译。
    输入：paragraphs —— list[str]，阿语段落列表，可为空；
          term_hits / name_hits —— 术语/专名命中列表（构建约束用），
          可为 None。
    输出：list[str] —— 与输入同长度、同顺序的译文列表；
          空输入返回 []（api 模式同样不发出任何请求）。
    异常：api 模式任一阶段失败（网络/业务/解析）抛对应 TranslationError
          子类（携带段落号，中断整批）；环境变量非法抛 ValueError。
    """
    # 空输入直接返回空列表：不加载配置、不发请求（契约：api 模式也如此）
    if not paragraphs:
        return []

    # 加载配置（读取环境变量；非法值在此抛 ValueError，由 UI 层提示）
    config = load_translation_config()

    # 回退条件：mock 模式本来就不联网；api 模式缺凭证也回退占位
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return _translate_with_mock(paragraphs)

    # api 模式：逐段串行翻译，一次调用翻译一段；
    # 某段失败即抛异常中断整批（异常携带该段段落号）
    translations = []
    for i, paragraph in enumerate(paragraphs):
        # 段落号 1 基；每段构建只含本段命中的约束文本（未来接 LLM 用）
        constraints = build_translation_constraints(
            term_hits, name_hits, paragraph_no=i + 1
        )
        translations.append(
            _translate_with_api(paragraph, constraints, config, i + 1)
        )
    return translations


def translate_paragraphs_parallel(
    paragraphs: list[str],
    term_hits: list[dict] | None = None,
    name_hits: list[dict] | None = None,
    *,
    on_translation: Callable[[int, str], None] | None = None,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> list[str]:
    """并行翻译段落，供 Streamlit 页面动态展示“翻译中/已翻译”。

    作用：与 translate_paragraphs 功能一致（mock 回退、api 真实翻译），
          但 api 模式下用线程池同时发起多个段落翻译请求，从而显著缩短
          多段文本的总等待时间。每完成一段就调用 on_translation(index,
          translation)（index 为 0 基；主线程调用，可安全更新 Streamlit
          empty 占位符）。
          LLM 纠正是整篇文章级别的任务，仍需等全部段落翻译完后再进行；
          因此本函数不处理 LLM 步骤。
    输入：paragraphs —— 阿语段落列表；term_hits / name_hits —— 命中列表；
          on_translation —— 可选回调（index, translation）；
          max_workers —— 并发线程数，默认 4。
    输出：list[str] —— 与输入同长度、同顺序的译文列表。
    异常：与 translate_paragraphs 一致：api 模式任一失败抛对应
          TranslationError 子类（携带段落号）；环境变量非法抛 ValueError。
    """
    # 空输入直接返回空列表：不加载配置、不发请求（契约同串行版）
    if not paragraphs:
        return []

    # 加载配置（读取环境变量；非法值在此抛 ValueError，由 UI 层提示）
    config = load_translation_config()

    # 回退条件：mock 模式或 api 缺凭证 → 占位，不联网；这时无需并发
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return _translate_with_mock(paragraphs)

    # api 模式：把所有段落提交给线程池，主线程按“先完成先回调”逐个展示。
    # 结果存进预分配列表，最终按原段落顺序返回。
    translations: list[str | None] = [None] * len(paragraphs)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_to_index = {}
    for i, paragraph in enumerate(paragraphs):
        # 段落号 1 基；每段构建只含本段命中的约束文本
        constraints = build_translation_constraints(
            term_hits, name_hits, paragraph_no=i + 1
        )
        future_to_index[
            executor.submit(
                _translate_with_api, paragraph, constraints, config, i + 1
            )
        ] = i

    try:
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            # future.result() 会把子线程里的异常原样抛出（含段落号）
            translated = future.result()
            translations[index] = translated
            # 主线程回调：Streamlit 的 st.empty 只能在主脚本线程安全更新
            if on_translation is not None:
                on_translation(index, translated)
    except Exception:
        # 任一段失败：取消尚未开始的请求，尽量体现“一段失败中断整批”；
        # 已在其他线程进行中的请求无法强行中止，但不会新增后续请求。
        for future in future_to_index:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    # ThreadPoolExecutor 返回顺序不影响这里：translations 已按原索引填好
    return [item for item in translations if item is not None]


def _translate_with_mock(paragraphs: list[str]) -> list[str]:
    """生成占位译文（mock 模式；不联网）。

    作用：为每个输入段落生成一条「（占位译文·第N段）待接入翻译 API」。
          文案与阶段 1 逐字节一致，保证阶段 1 的旧测试断言零改动。
    输入：paragraphs —— 阿语段落列表（可为空）。
    输出：list[str] —— 与输入同长度、同顺序的占位译文列表；
          空输入返回 []。
    """
    # 列表推导式按段落数生成占位译文，段号从 1 开始
    return [f"（占位译文·第{i + 1}段）待接入翻译 API" for i in range(len(paragraphs))]


# ---------------------------------------------------------------------------
# 阿里云 RPC 签名（纯函数，可单测）
# ---------------------------------------------------------------------------

def _percent_encode(text: str) -> str:
    """URL 百分号编码（阿里云签名规范规定的编码规则）。

    作用：把任意文本转成阿里云要求的编码形式——ASCII 字母数字与
          - _ . ~ 四个符号原样保留，其余字符按 UTF-8 字节百分号编码
          （空格 → %20、/ → %2F，十六进制一律大写）。
    输入：text —— 待编码的字符串。
    输出：str —— 编码后的字符串。
    """
    # urllib.parse.quote(text, safe="-_.~")：按 URL 编码规则转义；
    # 参数 safe 指定「不转义」的字符集，默认 safe="/" 会把 / 放行，
    # 而阿里云要求 / 必须转义成 %2F，所以这里显式覆盖为 -_.~ 四个符号。
    return urllib.parse.quote(text, safe="-_.~")


def _canonicalized_query_string(params: dict) -> str:
    """把参数字典规范化为查询串（键按字典序，各自编码）。

    作用：阿里云签名第一步——所有参数（除 Signature 外）按键名
          字典序排序，每个键值对编码为 k=v 后用 & 连接。
          规范化后的串既是签名输入，也是 POST 请求的 body
          （保证「发送字节 == 签名字节」）。
    输入：params —— 参数字典（键为字符串；值转字符串后参与编码）。
    输出：str —— 规范化查询串。
    """
    # sorted(params)：按键名升序（Python 按 Unicode 码点，对 ASCII 键
    # 即字节序，与阿里云要求的字典序一致）；逐项编码后拼接。
    items = []
    for key in sorted(params):
        # 键和值都要编码；值统一转成字符串（如 int 型参数）
        items.append(f"{_percent_encode(key)}={_percent_encode(str(params[key]))}")
    return "&".join(items)


def _build_string_to_sign(method: str, canonical: str) -> str:
    """构造待签名串 StringToSign（阿里云 RPC 签名规范）。

    作用：按「HTTPMethod & "/" & CanonicalizedQueryString」拼装，
          其中 / 写为编码形式 %2F；canonical 查询串要**整体再编码一次**
          ——于是 canonical 里原有的 % 变成 %25、& 变成 %26、
          %2F 变成 %252F（这就是签名串里出现 %252F 双重编码的固定特征，
          切勿手动把 %252F 还原成 %2F，否则签名不匹配）。
    输入：method —— HTTP 方法（本模块恒为 "POST"，大写）；
          canonical —— 规范化查询串（_canonicalized_query_string 的输出）。
    输出：str —— 待签名串。
    """
    # f-string 拼装三段；中间的 %2F 是字面量（/ 的编码形式，不再编码）
    return f"{method}&%2F&{_percent_encode(canonical)}"


def _compute_signature(secret: str, string_to_sign: str) -> str:
    """计算签名 Signature（base64(hmac-sha1(secret+"&", string_to_sign))）。

    作用：阿里云签名的最后一步——用「AccessKeySecret + &」作为密钥，
          对 StringToSign 做 HMAC-SHA1 摘要，再 base64 编码成文本
          Signature 参数。
    输入：secret —— 阿里云 AccessKey Secret（仅在本函数内参与运算，
          不会出现在任何日志或错误消息中）；
          string_to_sign —— _build_string_to_sign 的输出。
    输出：str —— base64 编码后的签名串（ASCII 文本）。
    """
    # hmac.new(key, msg, digestmod)：构造 HMAC 计算器；
    # key 与 msg 都需转成 UTF-8 字节串（hmac 只接受 bytes）。
    digest = hmac.new(
        (secret + "&").encode("utf-8"),  # 密钥 = Secret + "&"（阿里云规则）
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    # base64.b64encode(bytes)：二进制 → ASCII 文本；
    # .decode("ascii")：把 bytes 转成 str（便于放进请求参数）
    return base64.b64encode(digest).decode("ascii")


# ---------------------------------------------------------------------------
# 请求构造与发送
# ---------------------------------------------------------------------------

def _build_request_payload(
    source_text: str, config: TranslationConfig, constraints: str
) -> dict:
    """构造一次翻译请求的完整参数（含签名）。

    作用：按阿里云 TranslateGeneral 接口组装公共参数（Action/Version/
          Format/AccessKeyId/签名方法/签名版本/SignatureNonce/时间戳）
          与业务参数（FormatType/Scene/SourceLanguage/TargetLanguage/
          SourceText），并对「不含 Signature 的全体参数」计算签名后
          把 Signature 放回参数字典。
    输入：source_text —— 待翻译的阿语段落文本；
          config —— 翻译配置（提供凭证、语言、场景等）；
          constraints —— 术语约束文本（阶段 2 决策 3：暂不发送，
          仅预留参数位，见下方注释）。
    输出：dict —— 完整请求参数（含 Signature 键）。
    """
    params = {
        # ---- 公共参数（阿里云 RPC 调用规范，每个接口都要求）----
        "Action": "TranslateGeneral",        # 要调用的接口名
        "Version": "2018-10-12",             # 接口版本号
        "Format": "JSON",                    # 响应格式
        "AccessKeyId": config.access_key_id, # 凭证 ID（签名参与方之一）
        "SignatureMethod": "HMAC-SHA1",      # 签名算法（与 _compute_signature 配套）
        "SignatureVersion": "1.0",           # 签名规范版本
        # SignatureNonce：本次请求唯一的随机串，阿里云用它防重放攻击；
        # uuid.uuid4() 生成 128 位随机 UUID，转字符串即可保证每次不同
        "SignatureNonce": str(uuid.uuid4()),
        # Timestamp：ISO8601 格式的 UTC 时间（如 2026-08-08T12:00:00Z），
        # 与阿里云服务器时间差超过 15 分钟会返回请求过期错误
        "Timestamp": _utc_now_iso8601(),
        # ---- 业务参数（TranslateGeneral 接口专用）----
        "FormatType": "text",                # 文本格式
        "Scene": config.scene,               # 翻译场景（general 等）
        "SourceLanguage": config.source_lang, # 源语言代码（ar）
        "TargetLanguage": config.target_lang, # 目标语言代码（zh）
        "SourceText": source_text,           # 待翻译文本
    }
    # 术语约束预留位（阶段 2 决策 3）：已核实 TranslateGeneral 无
    # context/术语类参数，本阶段不发送约束文本。未来接入 LLM 审校时
    # 启用下面这一行即可：
    # params["Context"] = constraints
    # 注意：启用后无需改动签名逻辑——签名在下方对 params 全量参数
    # 统一计算，新参数会自动纳入签名，不需要手动处理。

    # ---- 签名步骤（顺序固定，不能颠倒）----
    # 1. 对「不含 Signature 的参数」做规范化（排序 + 编码）；
    #    Signature 本身不参与签名（否则签名算不出来）
    canonical = _canonicalized_query_string(params)
    # 2. 构造待签名串
    string_to_sign = _build_string_to_sign("POST", canonical)
    # 3. 计算签名并放回参数字典（发送时连同 Signature 一起编码）
    params["Signature"] = _compute_signature(config.access_key_secret, string_to_sign)
    return params


def _utc_now_iso8601() -> str:
    """生成 ISO8601 格式的当前 UTC 时间串。

    作用：为请求时间戳（Timestamp）提供统一格式：%Y-%m-%dT%H:%M:%SZ
          （如 2026-08-08T12:34:56Z），这是阿里云要求的格式。
    输入：无。
    输出：str —— 形如 2026-08-08T12:34:56Z 的 UTC 时间串。
    """
    # datetime.now(timezone.utc)：取当前 UTC 时间（带时区，避免本地时区偏差）；
    # strftime("...")：按格式字符串排版；结尾 Z 表示 UTC（零时区）
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _translate_with_api(
    paragraph: str,
    constraints: str,
    config: TranslationConfig,
    paragraph_no: int,
) -> str:
    """翻译单个段落（api 模式：构造 payload → POST → 解析）。

    作用：对一段文本执行一次阿里云翻译请求。请求体直接用
          _canonicalized_query_string(payload) 的字符串形式发送，
          保证「发送的字节 == 签名时用的字节」（任何重排/重编码
          都会导致 SignatureDoesNotMatch）。
    输入：paragraph —— 待翻译段落文本；constraints —— 该段约束文本
          （暂未发送，仅透传）；config —— 翻译配置；paragraph_no ——
          段落号（1 基）。
    输出：str —— 翻译后的文本。
    异常：网络失败（连接/超时/HTTP 非 200）→ TranslationNetworkError；
          业务错误（Code≠"200"）→ TranslationBusinessError；
          解析失败（非 JSON / 缺 Data.Translated）→ TranslationParseError；
          以上异常全部携带 paragraph_no。
    """
    # 组装完整参数（含签名）；body 用规范化字符串，与签名输入完全一致
    payload = _build_request_payload(paragraph, config, constraints)
    body = _canonicalized_query_string(payload)

    # requests.post(url, data=body, headers=..., timeout=...)：
    #   data 传字符串而非 dict —— 这样 requests 不会重排/重编码参数，
    #   保证发送字节与签名字节一致；
    #   headers 显式声明表单类型（阿里云按此解析 body）；
    #   timeout=config.timeout_seconds：单请求超时秒数（防挂死）。
    try:
        response = requests.post(
            config.endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=config.timeout_seconds,
        )
    except requests.exceptions.Timeout as e:
        # 超时：requests 把读超时/连超时都归入 Timeout 异常
        raise TranslationNetworkError(
            f"第{paragraph_no}段翻译请求超时（{config.timeout_seconds} 秒）",
            paragraph_no,
        ) from e
    except requests.exceptions.RequestException as e:
        # 连接失败（DNS/拒连/断线等）：RequestException 是 requests
        # 所有网络异常的基类；错误消息只带原因，绝不含密钥
        raise TranslationNetworkError(
            f"第{paragraph_no}段翻译请求连接失败：{e}",
            paragraph_no,
        ) from e

    # HTTP 状态码非 200 视为网络层失败（阿里云网关错误时返回 4xx/5xx）
    if response.status_code != 200:
        raise TranslationNetworkError(
            f"第{paragraph_no}段翻译请求 HTTP 状态码异常：{response.status_code}",
            paragraph_no,
        )

    # 解析响应（业务错误 / 格式错误都在这里转为对应异常）
    return _parse_response(response, paragraph_no)


def _parse_response(response, paragraph_no: int) -> str:
    """解析翻译接口的响应体。

    作用：把阿里云响应逐层校验后取出译文：
          1) 非合法 JSON → TranslationParseError；
          2) Code 存在且 ≠ "200" → TranslationBusinessError（附阿里云错误码）；
          3) 缺 Data.Translated → TranslationParseError。
    输入：response —— requests 的响应对象（有 .json() 方法）；
          paragraph_no —— 段落号（异常需携带）。
    输出：str —— 译文文本。
    """
    try:
        # response.json()：把响应体按 JSON 解析成 dict；
        # 响应体不是合法 JSON 时抛 ValueError（requests 的 JSONDecodeError 是它的子类）
        data = response.json()
    except ValueError as e:
        raise TranslationParseError(
            f"第{paragraph_no}段翻译响应不是合法 JSON", paragraph_no
        ) from e

    # dict.get(key)：键不存在时返回 None，不抛 KeyError
    code = data.get("Code")
    if code is not None and code != "200":
        # 业务层报错：如 InvalidAccessKeyId（凭证错）、ServiceNotOpened
        # （未开通服务）、SignatureDoesNotMatch（签名不匹配）。
        # Message 是阿里云给出的中文/英文说明，一并放进错误消息便于排查；
        # 消息里只有错误码与说明，没有任何密钥。
        message = data.get("Message", "")
        raise TranslationBusinessError(
            f"第{paragraph_no}段翻译业务错误（阿里云错误码：{code}）：{message}",
            paragraph_no,
            aliyun_code=code,
        )

    # 成功响应形如 {"Code": "200", "Data": {"Translated": "译文"}}；
    # Data 缺 Translated 字段说明接口协议有变，按解析失败处理
    data_block = data.get("Data")
    if not isinstance(data_block, dict) or "Translated" not in data_block:
        raise TranslationParseError(
            f"第{paragraph_no}段翻译响应缺少 Data.Translated 字段",
            paragraph_no,
        )
    return data_block["Translated"]
