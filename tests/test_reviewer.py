# -*- coding: utf-8 -*-
"""reviewer 模块的单元测试（阶段 3：LLM 审校双模式）。

测试对象：modules.reviewer 的以下内容——
- generate_review_report（双模式入口：mock 占位 / api 调 LLM）
- 异常体系（ReviewError 家族）
- 模板加载与占位符替换、命中清单格式化、请求构造与响应解析
- settings.load_review_config（审校配置加载）

运行方式：在项目根目录执行 `python -m pytest tests/test_reviewer.py -v`
（从根目录运行才能 import modules 下的模块）。

测试约定（阶段 3 起强制）：
- 不 import app.py（UI 层不在本文件测试范围）；
- 所有 Key 一律用假值（monkeypatch.setenv("DEEPSEEK_API_KEY", "fake_key")）；
- 网络请求一律用 monkeypatch.setattr(reviewer.requests, "post", fake_post)
  拦截，配合假响应对象（status_code / .json() / text）；
- 模板一律用 monkeypatch.setattr(reviewer, "_PROMPT_PATH", tmp_path/"prompt.md")
  指向测试专用模板（含 4 个占位符与「## 用户消息」标记）；
- 凡是「必须是 mock 模式」的测试，都显式 setenv REVIEW_ENGINE=mock，
  防止开发者 shell 环境里的真实环境变量干扰结果；
- 全程不打真实网络、不出现真实密钥。
"""

import pytest  # 测试框架：pytest.raises 断言异常用

from modules import reviewer, settings
# 阶段 1 保留测试沿用原有裸名导入（保证其函数体与阶段 1 逐字一致）
from modules.reviewer import generate_review_report


# ---------------------------------------------------------------------------
# 测试辅助：假响应对象、假 POST、环境变量与模板
# ---------------------------------------------------------------------------

class _FakeResponse:
    """假响应对象：模拟 requests.post 的返回值。

    作用：只实现测试用到的三个成员——status_code（HTTP 状态码）、
          .json()（解析响应体）与 text（原始响应文本），让被测代码
          不依赖真实网络。
    输入：status_code —— 模拟的 HTTP 状态码；
          json_data —— .json() 应返回的 dict（None 表示无响应体）；
          json_error —— .json() 应抛出的异常（模拟「不是合法 JSON」）；
          text —— 原始响应文本（业务错误消息里会截取前 200 字符）。
    输出：实例；.json() 按上面两种设置决定返回或抛出。
    """

    def __init__(self, status_code=200, json_data=None, json_error=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self._json_error = json_error
        self.text = text

    def json(self):
        # 模拟 requests 的行为：响应体不是合法 JSON 时抛 ValueError
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


def _fixed_fake_post(response):
    """构造记录型假 POST：每次调用返回同一个响应，并记录调用明细。

    作用：供「成功调用 LLM」类测试使用——每次调用都返回同一个成功
          响应，同时把每次调用的 url/json/headers/timeout 记进 calls，
          供断言调用次数、请求内容与请求头。
    输入：response —— 每次调用都返回的 _FakeResponse 实例。
    输出：(fake_post, calls) —— fake_post 是替换 reviewer.requests.post
          的函数；calls 是记录列表（每次调用追加一个 dict）。
    """

    calls = []

    def fake_post(url, **kwargs):
        # 记录调用明细：url 请求地址、json 请求体、headers 请求头、
        # timeout 超时秒数（kwargs.get 取不到时补 None，防 KeyError）
        calls.append(
            {
                "url": url,
                "json": kwargs.get("json"),
                "headers": kwargs.get("headers"),
                "timeout": kwargs.get("timeout"),
            }
        )
        return response

    return fake_post, calls


def _set_api_review_env(monkeypatch):
    """把审校环境变量设成「api 模式 + 假密钥」。

    作用：让 generate_review_report 内部 load_review_config 走 api 分支。
    输入：monkeypatch —— pytest 的 monkeypatch 夹具（测试结束自动还原）。
    输出：无（直接修改环境变量）。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, settings.API_ENGINE)
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_KEY, "fake_key")


def _write_template(monkeypatch, tmp_path, content=None):
    """写入测试专用提示词模板并把 _PROMPT_PATH 指向它。

    作用：让被测代码读取到「含 4 个占位符 + 「## 用户消息」标记」的
          测试模板，而不去碰项目里的真实模板文件。
    输入：monkeypatch —— pytest 夹具；tmp_path —— pytest 临时目录；
          content —— 模板内容（None 时用默认测试模板）。
    输出：Path —— 写入的模板文件路径。
    """
    if content is None:
        content = (
            "# 测试模板\n"
            "## 系统消息\n"
            "你是一位阿语-中文审校专家。\n"
            "## 用户消息\n"
            "原文段落：{source_paragraphs}\n"
            "译文段落：{translations}\n"
            "术语命中：{term_hits}\n"
            "专名命中：{name_hits}\n"
        )
    prompt_path = tmp_path / "prompt.md"
    # 显式指定 UTF-8 写入（Windows 默认 GBK，中文模板必须显式编码）
    prompt_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(reviewer, "_PROMPT_PATH", prompt_path)
    return prompt_path


# 固定的成功响应：OpenAI 兼容格式 choices[0].message.content 即报告文本
_OK_REVIEW_RESPONSE = _FakeResponse(
    200, {"choices": [{"message": {"content": "（测试审校报告）"}}]}
)


# ---------------------------------------------------------------------------
# 阶段 1 保留测试（函数体/断言/注释零改动）
# ---------------------------------------------------------------------------

def test_report_contains_counts():
    """验证报告文本中包含流水线统计数字（去重条数 + 累计次数）。

    作用：确认占位报告把段落数、术语命中条数（去重）与累计次数、
          专名命中条数（去重）与累计次数回填进了文本，
          即「切分 → 扫描 → 译文 → 报告」链路的数据已贯通到报告层。
    输入：2 段、术语命中 3 条（count 分别为 2/1/1，累计 4 次）、
          专名命中 1 条（count 为 2，累计 2 次）。
    输出：报告字符串分别包含「2 段」「术语命中 3 条（共 4 次）」
          「专名命中 1 条（共 2 次）」；任一断言失败即测试失败。
    """
    # 构造 2 段阿语段落与对应的 2 条译文
    paragraphs = ["段落A", "段落B"]
    translations = ["译文A", "译文B"]
    # 构造 3 条术语命中（累计 2+1+1=4 次）与 1 条专名命中（累计 2 次）
    term_hits = [{"term": "术语1", "count": 2}, {"term": "术语2", "count": 1}, {"term": "术语3", "count": 1}]
    name_hits = [{"name": "专名1", "count": 2}]
    # 调用被测函数，生成占位审校报告
    report = generate_review_report(paragraphs, translations, term_hits, name_hits)
    # 断言报告文本包含条数与次数两类统计数字
    assert "2 段" in report
    assert "术语命中 3 条（共 4 次）" in report
    assert "专名命中 1 条（共 2 次）" in report


def test_report_contains_placeholder_marker():
    """验证报告包含「占位」标记字样。

    作用：确认返回的是阶段 1 占位报告而非空串，便于前端识别占位状态。
    输入：1 段、术语命中 0 条、专名命中 0 条。
    输出：报告字符串包含「占位」；断言失败即测试失败。
    """
    # 构造单段输入，命中列表为空，调用被测函数
    report = generate_review_report(["段落"], ["译文"], [], [])
    # 断言占位标记「占位」出现在报告中
    assert "占位" in report


def test_report_is_string():
    """验证返回值是 str 类型。

    作用：确认接口返回类型符合约定（str），可直接在 Streamlit 中渲染。
    输入：四个参数全部为空列表（边界情况）。
    输出：返回值为 str 类型；断言失败即测试失败。
    """
    # 调用被测函数，四个输入均为空列表
    report = generate_review_report([], [], [], [])
    # 断言返回类型为 str
    assert isinstance(report, str)


# ---------------------------------------------------------------------------
# 审校配置加载（6 条）
# ---------------------------------------------------------------------------

def test_review_config_defaults(monkeypatch):
    """验证未设置任何审校环境变量时，配置取全部默认值。

    作用：确认 load_review_config 的默认值兜底——engine=mock、空密钥、
          默认 URL/模型/超时，且 has_credentials 为 False。
    输入：全部相关环境变量被清空（monkeypatch.delenv）。
    输出：配置对象字段与默认常量逐一相等。
    """
    # 清空全部相关环境变量（raising=False：变量不存在时不报错），
    # 防止开发者 shell 里的真实配置污染默认值断言
    for var in (
        settings.ENV_REVIEW_ENGINE,
        settings.ENV_DEEPSEEK_API_KEY,
        settings.ENV_DEEPSEEK_API_URL,
        settings.ENV_REVIEW_MODEL,
        settings.ENV_REVIEW_TIMEOUT_SECONDS,
    ):
        monkeypatch.delenv(var, raising=False)

    config = settings.load_review_config()
    assert config.engine == settings.MOCK_ENGINE
    assert config.api_key == ""
    assert config.api_url == settings.DEFAULT_DEEPSEEK_API_URL
    assert config.model == settings.DEFAULT_REVIEW_MODEL
    assert config.timeout_seconds == settings.DEFAULT_REVIEW_TIMEOUT
    # 无凭证时 has_credentials 必须为 False（决定是否回退占位报告）
    assert config.has_credentials is False


def test_review_config_reads_env_and_strips(monkeypatch):
    """验证审校配置从环境变量读取，且值被 strip 掉首尾空白。

    作用：确认环境变量 → 配置对象的传递链路，以及「所有值读取后
          .strip()」的约定（防手工填写带入空格）。
    输入：各变量带首尾空格的假值。
    输出：配置对象各字段为 strip 后的值。
    """
    # 每个假值都故意带首尾空格，验证 strip 生效
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, " api ")
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_KEY, "  fake_key  ")
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_URL, "  https://api.deepseek.com/  ")
    monkeypatch.setenv(settings.ENV_REVIEW_MODEL, " deepseek-chat ")
    monkeypatch.setenv(settings.ENV_REVIEW_TIMEOUT_SECONDS, " 45 ")

    config = settings.load_review_config()
    assert config.engine == "api"
    assert config.api_key == "fake_key"
    assert config.api_url == "https://api.deepseek.com/"
    assert config.model == "deepseek-chat"
    assert config.timeout_seconds == 45
    # 密钥非空 → has_credentials 必须为 True
    assert config.has_credentials is True


def test_review_config_invalid_engine_raises(monkeypatch):
    """验证非法审校引擎值抛 ValueError；空串回落默认 mock。

    作用：确认配置校验——REVIEW_ENGINE 只允许 mock/api，其他非空值
          （如 "llm"）抛 ValueError，由 UI 层转成配置错误提示；
          空串（.env 模板留空）视为未设置，回落到默认 mock，不报错。
    输入：REVIEW_ENGINE 分别设为 "llm"（应抛）与 ""（应回落）。
    输出：前者抛 ValueError；后者返回 engine=mock。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, "llm")
    # pytest.raises(ValueError)：断言后续代码必然抛出 ValueError
    with pytest.raises(ValueError):
        settings.load_review_config()

    # 空串（strip 后仍为空）= 模板留空 = 未设置 → 默认 mock，不抛错
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, "")
    config = settings.load_review_config()
    assert config.engine == settings.MOCK_ENGINE


def test_review_config_invalid_timeout_raises(monkeypatch):
    """验证非法审校超时值抛 ValueError；空串回落默认。

    作用：确认超时校验——非整数（"abc"）、0、负数都是非法配置；
          空串（模板留空）视为未设置，回落默认超时。
    输入：REVIEW_TIMEOUT_SECONDS 分别设为 "abc" / "0" / "-5"（应抛）
          与 ""（应回落）。
    输出：前三次调用抛 ValueError；空串返回默认超时。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, "mock")
    for bad_value in ("abc", "0", "-5"):
        monkeypatch.setenv(settings.ENV_REVIEW_TIMEOUT_SECONDS, bad_value)
        with pytest.raises(ValueError):
            settings.load_review_config()

    # 空串 = 模板留空 = 未设置 → 默认超时，不抛错
    monkeypatch.setenv(settings.ENV_REVIEW_TIMEOUT_SECONDS, "")
    config = settings.load_review_config()
    assert config.timeout_seconds == settings.DEFAULT_REVIEW_TIMEOUT


def test_review_config_url_empty_falls_back_to_default(monkeypatch):
    """验证审校 URL 空串回落默认值。

    作用：确认 api_url 遵循「空串 = 未设置 = 默认值」约定——
          填了空串（.env 模板留空）时用默认 DeepSeek 地址。
    输入：REVIEW_ENGINE=api + 假密钥 + DEEPSEEK_API_URL=""。
    输出：api_url 等于默认常量 DEFAULT_DEEPSEEK_API_URL。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, settings.API_ENGINE)
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_KEY, "fake_key")
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_URL, "")

    config = settings.load_review_config()
    assert config.api_url == settings.DEFAULT_DEEPSEEK_API_URL
    assert config.model == settings.DEFAULT_REVIEW_MODEL


def test_review_config_api_key_empty_keeps_empty(monkeypatch):
    """验证 api_key 空串刻意不回落（空串正是「缺凭证 → 回退占位」判定依据）。

    作用：确认 api_key 与 AK/SK 同策略——与其他字段的「空串回落默认值」
          不同，api_key 空串就是「无凭证」本身，绝不能回落成别的值，
          否则 api 缺 key 时会拿默认值当真密钥去请求。
    输入：REVIEW_ENGINE=api + DEEPSEEK_API_KEY=""。
    输出：api_key == "" 且 has_credentials 为 False。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, settings.API_ENGINE)
    monkeypatch.setenv(settings.ENV_DEEPSEEK_API_KEY, "")

    config = settings.load_review_config()
    assert config.api_key == ""
    assert config.has_credentials is False


# ---------------------------------------------------------------------------
# 双模式编排（4 条）
# ---------------------------------------------------------------------------

def test_review_mock_mode_makes_no_network_request(monkeypatch):
    """验证 mock 模式不发起任何网络请求。

    作用：确认 mock 模式的承诺——纯本地生成占位报告；若误发请求，
          假 POST 会抛 AssertionError 直接让测试失败。
    输入：REVIEW_ENGINE=mock（显式设定）；假 POST 见调用即抛。
    输出：2 段输入得到占位报告，且无人调用假 POST。
    """
    # 显式设定 mock 模式（防开发者 shell 环境变量干扰）
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, settings.MOCK_ENGINE)

    def fake_post(*args, **kwargs):
        # 只要被调用就抛错：mock 模式不允许出现任何网络请求
        raise AssertionError("mock 模式不应发起任何网络请求")

    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    report = reviewer.generate_review_report(["第1段", "第2段"], ["译文1", "译文2"], [], [])
    # 占位文案与阶段 1 逐字节一致
    assert "（占位）审校报告：待接入 LLM 后生成。" in report
    assert "2 段" in report


def test_review_api_missing_key_falls_back_to_mock(monkeypatch):
    """验证 api 模式缺密钥时回退占位报告且不联网，文案与 mock 逐字节相同。

    作用：确认「缺 key 不报错」决策——engine=api 但无凭证时走
          _generate_mock_report 并返回占位报告，页面据此显示黄色提示；
          回退报告与 mock 模式输出必须完全一致（逐字节相同）。
    输入：REVIEW_ENGINE=api、DEEPSEEK_API_KEY 被清空；假 POST 见调用即抛。
    输出：报告与 mock 输出逐字节相同；假 POST 未被调用。
    """
    monkeypatch.setenv(settings.ENV_REVIEW_ENGINE, settings.API_ENGINE)
    # 清掉可能存在的真实密钥，确保「无凭证」状态
    monkeypatch.delenv(settings.ENV_DEEPSEEK_API_KEY, raising=False)

    def fake_post(*args, **kwargs):
        raise AssertionError("缺密钥回退场景不应发起任何网络请求")

    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    report = reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])
    # 回退报告 = mock 报告（逐字节相同）
    assert report == (
        "（占位）审校报告：待接入 LLM 后生成。\n\n"
        "本次共 1 段，术语命中 0 条（共 0 次），专名命中 0 条（共 0 次）。"
    )


def test_review_api_empty_input_no_request(monkeypatch):
    """验证 api 模式空输入同样返回占位报告且不发请求。

    作用：锁定边界契约——即使 engine=api 且配齐密钥（已具备真调 LLM 的
          条件），空段落列表也直接返回占位报告（含「0 段」统计），
          不发起任何网络请求（与翻译契约对称）。
    输入：空列表 + api 模式 + 假密钥。
    输出：报告含「0 段」；且 reviewer.requests.post 一次都未被调用。
    """
    _set_api_review_env(monkeypatch)
    # 替换 post 为记录型假函数：被调用就记进 calls，并返回成功响应
    fake_post, calls = _fixed_fake_post(_OK_REVIEW_RESPONSE)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    report = reviewer.generate_review_report([], [], [], [])
    assert "0 段" in report
    assert calls == []


def test_review_api_with_key_success(monkeypatch, tmp_path):
    """验证 api 模式有密钥时调用 LLM 成功：恰好 1 次请求，返回报告文本。

    作用：确认 api 全链路（模板加载 → 数据填充 → 请求 → 解析）——
          有密钥时发出一次请求，响应里的 content 成为审校报告返回。
    输入：api 模式 + 假密钥 + 测试模板；假 POST 返回固定成功响应。
    输出：返回报告文本；calls 恰好 1 次调用。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)
    fake_post, calls = _fixed_fake_post(_OK_REVIEW_RESPONSE)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    report = reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])
    assert report == "（测试审校报告）"
    # 恰好 1 次请求（单次调用、无重试）
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 模板加载与占位符替换（4 条）
# ---------------------------------------------------------------------------

def test_review_render_replaces_placeholders(monkeypatch, tmp_path):
    """验证占位符全部替换：请求体含编号文本，不含字面占位符。

    作用：确认模板拆段与替换行为——系统消息截到「## 系统消息」标记
          之后（含角色设定、不含文件头部说明文字）；用户消息已编号化
          （「第1段：」），且 4 个占位符在发送的请求体里不再以字面量
          形式出现（全部被替换成真实数据）。
    输入：api 模式 + 假密钥 + 测试模板；2 段输入。
    输出：系统消息含角色设定且不含头部标题；用户消息含「第1段：」
          「第2段：」；不含任何 {占位符} 字面量。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)
    fake_post, calls = _fixed_fake_post(_OK_REVIEW_RESPONSE)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    reviewer.generate_review_report(["段落甲", "段落乙"], ["译文甲", "译文乙"], [], [])

    assert len(calls) == 1
    # OpenAI 兼容请求体：messages[0] 是系统消息、messages[1] 是用户消息
    system_message = calls[0]["json"]["messages"][0]["content"]
    # 系统消息应截到「## 系统消息」标记之后：含角色设定文本，
    # 不含文件头部（标题 + 维护者说明）——头部是给人看的说明，不是提示词
    assert "你是一位阿语-中文审校专家" in system_message
    assert "测试模板" not in system_message

    user_message = calls[0]["json"]["messages"][1]["content"]
    # 段落已编号化：出现「第1段：」「第2段：」
    assert "第1段：" in user_message
    assert "第2段：" in user_message
    # 4 个占位符都已被替换，正文里不允许再出现字面占位符
    for name in reviewer._PLACEHOLDERS:
        assert f"{{{name}}}" not in user_message


def test_review_template_missing_raises_file_not_found(monkeypatch, tmp_path):
    """验证模板文件缺失时抛 FileNotFoundError。

    作用：确认模板缺失的异常语义——文件不存在时由 open() 自然抛出
          FileNotFoundError（app.py 已捕获，提示数据文件缺失）。
    输入：_PROMPT_PATH 指向一个不存在的文件。
    输出：_load_prompt_template 抛 FileNotFoundError。
    """
    # 把 _PROMPT_PATH 指向不存在的文件（monkeypatch 测试后自动还原）
    monkeypatch.setattr(reviewer, "_PROMPT_PATH", tmp_path / "not_exist.md")

    with pytest.raises(FileNotFoundError):
        reviewer._load_prompt_template()


def test_review_template_missing_placeholder_raises(monkeypatch, tmp_path):
    """验证模板占位符缺失（或误挪进系统消息段）时抛 ValueError。

    作用：确认结构校验——4 个占位符必须全部位于「用户消息段」内且各
          恰好 1 次。两种情况都抛中文 ValueError：占位符整体缺失、
          以及占位符被误挪到系统消息段（全模板计数恰好 1 次但用户段
          没有——这是「只校验全模板」会漏掉的坏结构）。
    输入：(a) 用户段少 {name_hits}；(b) {term_hits} 被挪进系统消息段。
    输出：两种情况 _load_prompt_template 都抛 ValueError。
    """
    # 情况 (a)：用户消息段只写 3 个占位符（少 {name_hits}）
    content = (
        "# 测试模板\n## 系统消息\n专家。\n## 用户消息\n"
        "原文：{source_paragraphs}\n译文：{translations}\n术语：{term_hits}\n"
    )
    _write_template(monkeypatch, tmp_path, content)

    with pytest.raises(ValueError):
        reviewer._load_prompt_template()

    # 情况 (b)：{term_hits} 被误挪进系统消息段——全模板计数恰好 1 次，
    # 但用户消息段里没有它（校验对象必须是用户段才能抓住这类坏结构）
    content2 = (
        "# 测试模板\n## 系统消息\n专家。{term_hits}\n## 用户消息\n"
        "原文：{source_paragraphs}\n译文：{translations}\n专名：{name_hits}\n"
    )
    _write_template(monkeypatch, tmp_path, content2)

    with pytest.raises(ValueError):
        reviewer._load_prompt_template()


def test_review_template_missing_marker_raises(monkeypatch, tmp_path):
    """验证模板缺分隔标记（或标记顺序错乱）时抛 ValueError。

    作用：确认结构校验——「## 系统消息」与「## 用户消息」两个标记都
          必须存在（错误消息点名缺哪个），且系统标记在用户标记之前；
          任一违反都抛中文 ValueError。
    输入：三种坏模板——(a) 缺用户标记（占位符齐全也无效）；
          (b) 缺系统标记；(c) 两标记都在但顺序颠倒。
    输出：三种情况 _load_prompt_template 都抛 ValueError。
    """
    # 情况 (a)：缺「## 用户消息」标记
    content = (
        "# 测试模板\n## 系统消息\n专家。\n"
        "原文：{source_paragraphs}\n译文：{translations}\n术语：{term_hits}\n专名：{name_hits}\n"
    )
    _write_template(monkeypatch, tmp_path, content)

    with pytest.raises(ValueError):
        reviewer._load_prompt_template()

    # 情况 (b)：缺「## 系统消息」标记
    content2 = (
        "# 测试模板\n## 用户消息\n"
        "原文：{source_paragraphs}\n译文：{translations}\n术语：{term_hits}\n专名：{name_hits}\n"
    )
    _write_template(monkeypatch, tmp_path, content2)

    with pytest.raises(ValueError):
        reviewer._load_prompt_template()

    # 情况 (c)：两个标记都在，但顺序颠倒（用户标记在系统标记之前）
    content3 = (
        "# 测试模板\n## 用户消息\n"
        "原文：{source_paragraphs}\n译文：{translations}\n术语：{term_hits}\n专名：{name_hits}\n"
        "## 系统消息\n专家。\n"
    )
    _write_template(monkeypatch, tmp_path, content3)

    with pytest.raises(ValueError):
        reviewer._load_prompt_template()


# ---------------------------------------------------------------------------
# 命中清单格式化（3 条）
# ---------------------------------------------------------------------------

def test_format_hits_terms_six_keys():
    """验证术语命中按六列键全格式化，含「出现段落」「出现次数」。

    作用：确认术语行的完整格式——阿语原文/中文译文带「」引号，括号内
          类别/领域/备注/处理方式四列 + 出现段落 + 出现次数，段落列表
          用 ", " 连接。
    输入：1 条含全部六列键与 paragraphs/count 的术语命中。
    输出：格式化文本含全部键与期望值。
    """
    term_hits = [
        {"阿语原文": "فلسطين", "中文译文": "巴勒斯坦", "类别": "地名",
         "领域": "政治外交", "备注": "固定译法", "处理方式": "force_check",
         "paragraphs": [1, 4, 5], "count": 7},
    ]
    text = reviewer._format_hits_text(term_hits, [])
    assert "【术语命中】共 1 条" in text
    assert "阿语原文「فلسطين」→ 中文译文「巴勒斯坦」" in text
    assert "类别：地名" in text
    assert "领域：政治外交" in text
    assert "备注：固定译法" in text
    # 处理方式保留 CSV 原始值（不做 UI 映射）
    assert "处理方式：force_check" in text
    assert "出现段落：1, 4, 5" in text
    assert "出现次数：7" in text


def test_format_hits_names_four_keys():
    """验证专名命中按四列键格式化，不因缺少「领域」「处理方式」而抛 KeyError。

    作用：确认 .get 兼容两库键差异——专名库只有四列（无「领域」「处理
          方式」），格式化时这两列不输出、也不抛 KeyError。
    输入：1 条只含四列键与 paragraphs/count 的专名命中。
    输出：格式化文本含专名各键值；不含「领域」「处理方式」字样。
    """
    name_hits = [
        {"阿语原文": "مصر", "中文译文": "埃及", "类别": "国名",
         "备注": "常用译法", "paragraphs": [2], "count": 3},
    ]
    text = reviewer._format_hits_text([], name_hits)
    assert "【专名命中】共 1 条" in text
    assert "阿语原文「مصر」→ 中文译文「埃及」" in text
    assert "类别：国名" in text
    assert "备注：常用译法" in text
    assert "出现段落：2" in text
    assert "出现次数：3" in text
    # 专名没有这两列，输出中不应出现
    assert "领域" not in text
    assert "处理方式" not in text


def test_format_hits_empty_outputs_none():
    """验证无命中（含 None 输入）时输出「无」。

    作用：确认边界行为——命中列表为空或为 None 时，两类各自输出
          「【术语命中】无」「【专名命中】无」，不报错。
    输入：空列表组合与 None 组合。
    输出：两类输出「无」标记。
    """
    assert reviewer._format_hits_text([], []) == "【术语命中】无\n【专名命中】无"
    # None 输入同样视为无命中（脏数据兜底）
    assert reviewer._format_hits_text(None, None) == "【术语命中】无\n【专名命中】无"


# ---------------------------------------------------------------------------
# 请求构造（3 条）
# ---------------------------------------------------------------------------

def test_build_api_url_strips_trailing_slash():
    """验证 base URL 去尾斜杠后拼 /chat/completions。

    作用：确认 URL 拼接逻辑——无论配置里填的是带尾斜杠还是不带尾斜杠
          的 base URL，结果都恰好一个斜杠（避免 // 双斜杠）。
    输入：两种写法（带 / 与不带 /）的 api_url。
    输出：两种都得到 https://api.deepseek.com/chat/completions。
    """
    config = settings.ReviewConfig(
        engine=settings.API_ENGINE, api_key="fake_key",
        api_url="https://api.deepseek.com/", model="deepseek-chat",
        timeout_seconds=30,
    )
    assert reviewer._build_api_url(config) == "https://api.deepseek.com/chat/completions"

    config2 = settings.ReviewConfig(
        engine=settings.API_ENGINE, api_key="fake_key",
        api_url="https://api.deepseek.com", model="deepseek-chat",
        timeout_seconds=30,
    )
    assert reviewer._build_api_url(config2) == "https://api.deepseek.com/chat/completions"


def test_build_request_payload_structure():
    """验证请求体结构：model、双角色 messages、temperature==0.3、不含密钥。

    作用：确认 OpenAI 兼容请求体的完整结构——model 来自配置、messages
          是 system/user 双角色、temperature 固定 0.3；且请求体文本
          绝不包含密钥（密钥只在 HTTP 头里）。
    输入：假配置 + 两段消息文本。
    输出：payload 结构与期望逐一相等；"fake_key" 不在 payload 文本中。
    """
    config = settings.ReviewConfig(
        engine=settings.API_ENGINE, api_key="fake_key",
        api_url="https://api.deepseek.com", model="deepseek-chat",
        timeout_seconds=30,
    )
    payload = reviewer._build_request_payload(config, "系统消息", "用户消息")
    assert payload["model"] == "deepseek-chat"
    assert payload["messages"] == [
        {"role": "system", "content": "系统消息"},
        {"role": "user", "content": "用户消息"},
    ]
    assert payload["temperature"] == 0.3
    # 安全断言：请求体文本不得包含密钥
    assert "fake_key" not in str(payload)


def test_review_request_headers_url_timeout(monkeypatch, tmp_path):
    """验证请求的 URL/headers/timeout：Bearer 认证、JSON 类型、配置超时。

    作用：确认请求层细节——URL 为 base + /chat/completions；headers 带
          Bearer 假密钥认证与 JSON 内容类型；timeout 取配置值。
    输入：api 模式 + 假密钥 + 测试模板；假 POST 记录调用明细。
    输出：calls[0] 的 url/headers/timeout 与期望一致。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)
    fake_post, calls = _fixed_fake_post(_OK_REVIEW_RESPONSE)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])

    assert len(calls) == 1
    call = calls[0]
    # URL = 默认 base URL + /chat/completions
    assert call["url"] == settings.DEFAULT_DEEPSEEK_API_URL + "/chat/completions"
    # headers：Bearer 认证头 + JSON 内容类型
    assert call["headers"] == {
        "Authorization": "Bearer fake_key",
        "Content-Type": "application/json",
    }
    # 超时秒数来自配置（默认 30）
    assert call["timeout"] == settings.DEFAULT_REVIEW_TIMEOUT


# ---------------------------------------------------------------------------
# 错误路径（6 条）
# ---------------------------------------------------------------------------

def test_review_http_401_raises_business_error(monkeypatch, tmp_path):
    """验证 HTTP 非 200 抛 ReviewBusinessError，带状态码与响应片段、不含密钥。

    作用：确认业务失败的异常语义——HTTP 401 抛业务异常，携带状态码
          与响应文本片段（前 200 字符），且异常消息不含密钥。
    输入：api 模式；假响应 status_code=401、text 含一段错误说明。
    输出：抛 ReviewBusinessError；status_code==401；消息含状态码与片段；
          "fake_key" 不在消息中。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)
    err = _FakeResponse(status_code=401, json_data={}, text="invalid api key: authentication failed")
    fake_post, _ = _fixed_fake_post(err)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    with pytest.raises(reviewer.ReviewBusinessError) as exc_info:
        reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])

    e = exc_info.value
    assert e.status_code == 401
    # 消息包含状态码与响应片段
    assert "401" in str(e)
    assert "invalid api key" in str(e)
    # 安全断言：异常消息绝不能包含密钥
    assert "fake_key" not in str(e)


def test_review_timeout_raises_network_error(monkeypatch, tmp_path):
    """验证请求超时抛 ReviewNetworkError。

    作用：确认超时的异常语义——requests 抛 Timeout 时转为网络异常，
          消息含「超时」字样，页面提示「审校失败」。
    输入：api 模式；假 POST 抛 requests.exceptions.Timeout。
    输出：抛 ReviewNetworkError。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)

    def fake_post(*args, **kwargs):
        # 模拟超时：requests 在超时时抛 Timeout 异常
        raise reviewer.requests.exceptions.Timeout("连接超时")

    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    with pytest.raises(reviewer.ReviewNetworkError) as exc_info:
        reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])
    assert "超时" in str(exc_info.value)


def test_review_connection_error_raises_network_error(monkeypatch, tmp_path):
    """验证连接失败抛 ReviewNetworkError。

    作用：确认连接失败的异常语义——DNS 解析失败/拒连等抛网络异常。
    输入：api 模式；假 POST 抛 requests.exceptions.ConnectionError。
    输出：抛 ReviewNetworkError。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)

    def fake_post(*args, **kwargs):
        # 模拟连接失败：requests 在无法连接时抛 ConnectionError
        raise reviewer.requests.exceptions.ConnectionError("拒绝连接")

    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    with pytest.raises(reviewer.ReviewNetworkError) as exc_info:
        reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])
    assert "连接失败" in str(exc_info.value)


def test_review_invalid_json_raises_parse_error(monkeypatch, tmp_path):
    """验证响应非合法 JSON 抛 ReviewParseError。

    作用：确认解析层的异常语义——响应体不是合法 JSON 时抛解析异常。
    输入：api 模式；假响应 .json() 抛 ValueError。
    输出：抛 ReviewParseError。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)
    bad = _FakeResponse(200, json_error=ValueError("不是 JSON"))
    fake_post, _ = _fixed_fake_post(bad)
    monkeypatch.setattr(reviewer.requests, "post", fake_post)

    with pytest.raises(reviewer.ReviewParseError):
        reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])


def test_review_missing_choices_raises_parse_error(monkeypatch, tmp_path):
    """验证 200 但缺 choices（或 choices 不是列表/为空）抛 ReviewParseError。

    作用：确认 choices 层的校验——缺 choices 键、choices 不是列表、
          choices 为空列表都按解析失败处理。
    输入：api 模式；三种坏响应结构。
    输出：三种情况都抛 ReviewParseError。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)

    for bad_data in ({"model": "deepseek-chat"}, {"choices": "not-a-list"}, {"choices": []}):
        bad = _FakeResponse(200, bad_data)
        fake_post, _ = _fixed_fake_post(bad)
        monkeypatch.setattr(reviewer.requests, "post", fake_post)
        with pytest.raises(reviewer.ReviewParseError):
            reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])


def test_review_missing_content_raises_parse_error(monkeypatch, tmp_path):
    """验证 200 但缺 message.content 抛 ReviewParseError。

    作用：确认 content 层的校验——choices[0] 里缺 message、或 message
          缺 content 都按解析失败处理。
    输入：api 模式；三种坏响应结构。
    输出：三种情况都抛 ReviewParseError。
    """
    _set_api_review_env(monkeypatch)
    _write_template(monkeypatch, tmp_path)

    for bad_data in (
        {"choices": [{}]},
        {"choices": [{"message": {"role": "assistant"}}]},
        {"choices": [{"message": {}}]},
    ):
        bad = _FakeResponse(200, bad_data)
        fake_post, _ = _fixed_fake_post(bad)
        monkeypatch.setattr(reviewer.requests, "post", fake_post)
        with pytest.raises(reviewer.ReviewParseError):
            reviewer.generate_review_report(["阿语段落"], ["译文"], [], [])
