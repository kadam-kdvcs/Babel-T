# -*- coding: utf-8 -*-
"""translator 模块的单元测试（阶段 2：双模式翻译）。

测试对象：modules.translator 的以下内容——
- translate_paragraphs（双模式入口：mock 占位 / api 调阿里云）
- load_translation_config / build_translation_constraints
- RPC 签名四纯函数与 payload 构造
- 异常体系（TranslationError 家族）

运行方式：在项目根目录执行 `python -m pytest tests/test_translator.py -v`
（从根目录运行才能 import modules 下的模块）。

测试约定（阶段 2 起强制）：
- 不 import app.py（UI 层不在本文件测试范围）；
- 所有 Key 一律用假值（monkeypatch.setenv / 直接构造配置对象）；
- 网络请求一律用 monkeypatch.setattr(translator.requests, "post", fake_post)
  拦截，配合假响应对象（status_code / .json()）；
- 凡是「必须是 mock 模式」的测试，都显式 setenv TRANSLATION_ENGINE=mock，
  防止开发者 shell 环境里的真实环境变量干扰结果；
- 全程不打真实网络、不出现真实密钥。
"""

import base64  # 标准库：base64 编码（测试内 hmac 参考实现用）
import hashlib  # 标准库：sha1 摘要（测试内 hmac 参考实现用）
import hmac  # 标准库：HMAC 计算（测试内交叉验证签名用）
import urllib.parse  # 标准库：解析/编码查询串（解析假请求 body 用）
from datetime import datetime, timezone  # 标准库：校验 Timestamp 用
from uuid import UUID  # 标准库：校验 SignatureNonce 格式用

import pytest  # 测试框架：pytest.raises 断言异常用

from modules import settings, translator
# 阶段 1 保留测试沿用原有裸名导入（保证其函数体与阶段 1 逐字一致）
from modules.translator import translate_paragraphs


# ---------------------------------------------------------------------------
# 测试辅助：假响应对象与假 POST 函数
# ---------------------------------------------------------------------------

class _FakeResponse:
    """假响应对象：模拟 requests.post 的返回值。

    作用：只实现测试用到的两个成员——status_code（HTTP 状态码）
          与 .json()（解析响应体），让被测代码不依赖真实网络。
    输入：status_code —— 模拟的 HTTP 状态码；
          json_data —— .json() 应返回的 dict（None 表示无响应体）；
          json_error —— .json() 应抛出的异常（模拟「不是合法 JSON」）。
    输出：实例；.json() 按上面两种设置决定返回或抛出。
    """

    def __init__(self, status_code=200, json_data=None, json_error=None):
        self.status_code = status_code
        self._json_data = json_data
        self._json_error = json_error

    def json(self):
        # 模拟 requests 的行为：响应体不是合法 JSON 时抛 ValueError
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


def _fixed_fake_post(response):
    """构造记录型假 POST：每次调用返回同一个响应，并记录调用明细。

    作用：供「成功翻译 N 段」类测试使用——每段请求都返回同一个
          成功响应，同时把每次调用的 url/body/headers/timeout 记进
          calls，供断言调用次数、请求内容与顺序。
    输入：response —— 每次调用都返回的 _FakeResponse 实例。
    输出：(fake_post, calls) —— fake_post 是替换 translator.requests.post
          的函数；calls 是记录列表（每次调用追加一个 dict）。
    """

    calls = []

    def fake_post(url, **kwargs):
        # 记录调用明细：url 请求地址、data 请求体、headers 请求头、
        # timeout 超时秒数（kwargs.get 取不到时补 None，防 KeyError）
        calls.append(
            {
                "url": url,
                "data": kwargs.get("data"),
                "headers": kwargs.get("headers"),
                "timeout": kwargs.get("timeout"),
            }
        )
        return response

    return fake_post, calls


class _SequentialFakePost:
    """按调用次序返回不同响应的假 POST（带调用记录）。

    作用：供「第 N 段失败」类测试使用——前几次调用返回成功响应，
          后面返回错误响应，模拟「一段失败、中断整批」的场景。
    输入：responses —— _FakeResponse 列表，第 k 次调用返回
          responses[min(k, len(responses)-1)]（超出后复用最后一个）。
    输出：实例可直接当函数调用；.calls 是每次调用的 body 列表。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, **kwargs):
        # 记录本次请求的 body（供断言「第 N 段后未再发请求」）
        self.calls.append(kwargs.get("data"))
        # 超出响应列表长度时复用最后一个（后续调用仍会记录）
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _api_config(ak="fake_access_key_id", secret="fake_access_key_secret"):
    """构造 api 模式的配置对象（假凭证，绕过环境变量）。

    作用：payload / 纯函数类测试不需要走 load_translation_config，
          直接用假值构造配置对象，测试更独立、结果更确定。
    输入：ak / secret —— 假 AccessKey（默认值即可，绝不填真实密钥）。
    输出：settings.TranslationConfig 实例（engine=api）。
    """
    return settings.TranslationConfig(
        engine=settings.API_ENGINE,
        access_key_id=ak,
        access_key_secret=secret,
        endpoint=settings.DEFAULT_ENDPOINT,
        source_lang=settings.DEFAULT_SOURCE_LANG,
        target_lang=settings.DEFAULT_TARGET_LANG,
        scene=settings.DEFAULT_SCENE,
        timeout_seconds=settings.DEFAULT_TIMEOUT,
    )


def _set_api_env(monkeypatch):
    """把环境变量设成「api 模式 + 假凭证」。

    作用：让 translate_paragraphs 内部 load_translation_config 走 api 分支。
    输入：monkeypatch —— pytest 的 monkeypatch 夹具（测试结束自动还原）。
    输出：无（直接修改环境变量）。
    """
    monkeypatch.setenv(settings.ENV_ENGINE, settings.API_ENGINE)
    monkeypatch.setenv(settings.ENV_ACCESS_KEY_ID, "fake_access_key_id")
    monkeypatch.setenv(settings.ENV_ACCESS_KEY_SECRET, "fake_access_key_secret")


# ---------------------------------------------------------------------------
# 阶段 1 保留测试（断言零改动；仅新增显式 mock 模式设定）
# ---------------------------------------------------------------------------

def test_placeholder_length_matches(monkeypatch):
    """验证译文数量与输入段落数一致（3 段 → 3 条译文）。

    作用：确认占位翻译「一一对应」的数据流特性，N 段输入必然产生 N 条译文。
    输入：3 段阿语段落列表（阶段 1 不解析阿语内容，占位文本即可）。
    输出：translate_paragraphs 返回含 3 条译文的列表；断言失败即测试失败。
    """
    # 显式设定 mock 模式（防开发者 shell 环境变量干扰；断言与阶段 1 完全一致）
    monkeypatch.setenv("TRANSLATION_ENGINE", "mock")
    # 构造 3 段占位输入段落
    paragraphs = ["第1段", "第2段", "第3段"]
    # 调用被测函数，返回与输入一一对应的译文列表
    translations = translate_paragraphs(paragraphs)
    # 断言译文数量与输入段数一致
    assert len(translations) == 3


def test_placeholder_contains_marker(monkeypatch):
    """验证译文包含「占位译文」标记字样。

    作用：确认生成的是占位译文而非空串，且标记文案正确，便于前端识别占位状态。
    输入：1 段阿语段落。
    输出：译文首元素为包含「占位译文」的非空字符串；断言失败即测试失败。
    """
    # 显式设定 mock 模式（防开发者 shell 环境变量干扰；断言与阶段 1 完全一致）
    monkeypatch.setenv("TRANSLATION_ENGINE", "mock")
    # 构造单段输入
    paragraphs = ["测试段落"]
    # 调用被测函数
    translations = translate_paragraphs(paragraphs)
    # 断言占位标记「占位译文」出现在第一条译文中
    assert "占位译文" in translations[0]


def test_empty_input(monkeypatch):
    """验证空输入返回空列表（[] → []）。

    作用：确认边界行为——空段落列表不应报错，应返回空译文列表 []。
    输入：空列表 []。
    输出：[]；断言失败即测试失败。
    """
    # 显式设定 mock 模式（防开发者 shell 环境变量干扰；断言与阶段 1 完全一致）
    monkeypatch.setenv("TRANSLATION_ENGINE", "mock")
    # 调用被测函数，输入为空列表
    result = translate_paragraphs([])
    # 断言返回空列表
    assert result == []


def test_api_mode_empty_input_no_request(monkeypatch):
    """验证 api 模式空输入同样返回 [] 且不发任何请求。

    作用：锁定边界契约——即使 engine=api 且配齐凭证（已具备真调 API 的条件），
          空段落列表也直接返回 []，不发起任何网络请求。
    输入：空列表 [] + api 模式 + 假凭证。
    输出：[]；且 translator.requests.post 一次都未被调用。
    """
    # api 模式 + 完整假凭证（两个 key 都非空才满足「有凭证」条件）
    monkeypatch.setenv("TRANSLATION_ENGINE", "api")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "fake_access_key_id")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "fake_access_key_secret")
    # 替换 post 为记录型假函数：被调用就记进 calls，并返回成功响应
    fake_post, calls = _fixed_fake_post(
        _FakeResponse(200, {"Code": "200", "Data": {"Translated": "译文"}})
    )
    monkeypatch.setattr(translator.requests, "post", fake_post)
    # 调用被测函数，输入为空列表
    result = translate_paragraphs([])
    # 断言返回空列表，且网络请求一次都未发生
    assert result == []
    assert calls == []


# ---------------------------------------------------------------------------
# 配置加载（4 条）
# ---------------------------------------------------------------------------

def test_config_defaults(monkeypatch):
    """验证未设置任何环境变量时，配置取全部默认值。

    作用：确认 load_translation_config 的默认值兜底——engine=mock、
          空凭证、默认地址/语言/场景/超时，且 has_credentials 为 False。
    输入：全部相关环境变量被清空（monkeypatch.delenv）。
    输出：配置对象字段与默认常量逐一相等。
    """
    # 清空全部相关环境变量（raising=False：变量不存在时不报错），
    # 防止开发者 shell 里的真实配置污染默认值断言
    for var in (
        "TRANSLATION_ENGINE",
        "ALIYUN_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIYUN_MT_ENDPOINT",
        "TRANSLATION_SOURCE_LANG",
        "TRANSLATION_TARGET_LANG",
        "TRANSLATION_SCENE",
        "TRANSLATION_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)

    config = settings.load_translation_config()
    assert config.engine == settings.MOCK_ENGINE
    assert config.access_key_id == ""
    assert config.access_key_secret == ""
    assert config.endpoint == settings.DEFAULT_ENDPOINT
    assert config.source_lang == settings.DEFAULT_SOURCE_LANG
    assert config.target_lang == settings.DEFAULT_TARGET_LANG
    assert config.scene == settings.DEFAULT_SCENE
    assert config.timeout_seconds == settings.DEFAULT_TIMEOUT
    # 无凭证时 has_credentials 必须为 False（决定是否回退占位）
    assert config.has_credentials is False


def test_config_reads_env_and_strips(monkeypatch):
    """验证配置从环境变量读取，且值被 strip 掉首尾空白。

    作用：确认环境变量 → 配置对象的传递链路，以及「所有值读取后
          .strip()」的约定（防手工填写带入空格）。
    输入：各变量带首尾空格的假值。
    输出：配置对象各字段为 strip 后的值。
    """
    # 每个假值都故意带首尾空格，验证 strip 生效
    monkeypatch.setenv("TRANSLATION_ENGINE", " api ")  # 引擎值也允许带空格
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "  fake_access_key_id  ")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", " fake_access_key_secret ")
    monkeypatch.setenv("ALIYUN_MT_ENDPOINT", "  https://mt.aliyuncs.com/  ")
    monkeypatch.setenv("TRANSLATION_SOURCE_LANG", " ar ")
    monkeypatch.setenv("TRANSLATION_TARGET_LANG", " zh ")
    monkeypatch.setenv("TRANSLATION_SCENE", " general ")
    monkeypatch.setenv("TRANSLATION_TIMEOUT_SECONDS", " 45 ")

    config = settings.load_translation_config()
    assert config.engine == "api"
    assert config.access_key_id == "fake_access_key_id"
    assert config.access_key_secret == "fake_access_key_secret"
    assert config.endpoint == "https://mt.aliyuncs.com/"
    assert config.source_lang == "ar"
    assert config.target_lang == "zh"
    assert config.scene == "general"
    assert config.timeout_seconds == 45
    # 两个凭证都非空 → has_credentials 必须为 True
    assert config.has_credentials is True


def test_config_invalid_engine_raises(monkeypatch):
    """验证非法引擎值抛 ValueError；空串回落默认 mock。

    作用：确认配置校验——TRANSLATION_ENGINE 只允许 mock/api，
          其他非空值（如 "llm"）抛 ValueError，由 UI 层转成配置错误提示；
          空串（.env 模板留空）视为未设置，回落到默认 mock，不报错。
    输入：TRANSLATION_ENGINE 分别设为 "llm"（应抛）与 ""（应回落）。
    输出：前者抛 ValueError；后者返回 engine=mock。
    """
    monkeypatch.setenv(settings.ENV_ENGINE, "llm")
    # pytest.raises(ValueError)：断言后续代码必然抛出 ValueError
    with pytest.raises(ValueError):
        settings.load_translation_config()

    # 空串（strip 后仍为空）= 模板留空 = 未设置 → 默认 mock，不抛错
    monkeypatch.setenv(settings.ENV_ENGINE, "")
    config = settings.load_translation_config()
    assert config.engine == settings.MOCK_ENGINE


def test_config_invalid_timeout_raises(monkeypatch):
    """验证非法超时值抛 ValueError；空串回落默认。

    作用：确认超时校验——非整数（"abc"）、0、负数都是非法配置；
          空串（模板留空）视为未设置，回落默认超时。
    输入：TRANSLATION_TIMEOUT_SECONDS 分别设为 "abc" / "0" / "-5"（应抛）
          与 ""（应回落）。
    输出：前三次调用抛 ValueError；空串返回默认超时。
    """
    monkeypatch.setenv(settings.ENV_ENGINE, "mock")
    for bad_value in ("abc", "0", "-5"):
        monkeypatch.setenv(settings.ENV_TIMEOUT_SECONDS, bad_value)
        with pytest.raises(ValueError):
            settings.load_translation_config()

    # 空串 = 模板留空 = 未设置 → 默认超时，不抛错
    monkeypatch.setenv(settings.ENV_TIMEOUT_SECONDS, "")
    config = settings.load_translation_config()
    assert config.timeout_seconds == settings.DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# 签名纯函数（4 条）
# ---------------------------------------------------------------------------

def test_percent_encode_rules():
    """验证 _percent_encode 的编码规则。

    作用：确认阿里云要求的编码规则——空格→%20、/→%2F（默认 safe
          会放行 /，这里必须转义）、阿语按 UTF-8 字节百分号编码且
          十六进制大写、-_.~ 四个符号原样保留。
    输入：若干代表性字符串。
    输出：与期望编码串逐一相等。
    """
    # 空格与斜杠是最常踩坑的两个字符
    assert translator._percent_encode("a b/c") == "a%20b%2Fc"
    # 阿语 مرحبا（5 个字母）逐字节编码为大写十六进制
    assert translator._percent_encode("مرحبا") == "%D9%85%D8%B1%D8%AD%D8%A8%D8%A7"
    # 阿里云规定的「不转义」符号集：- _ . ~ 原样保留
    assert translator._percent_encode("a-b_c.d~e") == "a-b_c.d~e"
    # 大小写字母与数字不转义
    assert translator._percent_encode("AbC123") == "AbC123"


def test_canonicalized_query_string_sorted_golden():
    """验证 _canonicalized_query_string 的排序与编码（golden 值）。

    作用：确认规范化的两个关键行为——①键按字典序排序（输入乱序也要
          按 Action→SourceText→TargetLanguage 输出）；②每个键值对各自
          编码后以 & 连接。golden 值在实现时冻结，防回归。
    输入：乱序参数 dict（含阿语值）。
    输出：与冻结的 golden 串逐字节相等。
    """
    # 故意乱序传入：TargetLanguage 先出现，Action 最后出现
    params = {
        "TargetLanguage": "zh",
        "SourceText": "مرحبا",
        "Action": "TranslateGeneral",
    }
    expected = (
        "Action=TranslateGeneral"
        "&SourceText=%D9%85%D8%B1%D8%AD%D8%A8%D8%A7"
        "&TargetLanguage=zh"
    )
    assert translator._canonicalized_query_string(params) == expected


def test_string_to_sign_format_with_double_encoding():
    """验证 _build_string_to_sign 的格式（含 %252F 双重编码特征）。

    作用：确认待签名串的固定拼装——方法 + &%2F& + canonical 整体再编码
          一次：canonical 里的 & 变 %26、= 变 %3D，而既有 %2F 变成
          %252F（双重编码特征，还原成 %2F 会导致签名不匹配）。
    输入：两个 canonical 串（普通 / 自带 %2F 两种）。
    输出：与期望串逐字节相等。
    """
    # 普通 canonical：& 和 = 各编码一次
    assert translator._build_string_to_sign("POST", "a=1&b=2") == "POST&%2F&a%3D1%26b%3D2"
    # canonical 里已有 %2F（源文本含斜杠所致）→ 再编码为 %252F
    assert translator._build_string_to_sign("POST", "a=%2Fb") == "POST&%2F&a%3D%252Fb"


def test_compute_signature_known_answer():
    """验证 _compute_signature 的 known-answer（冻结 golden + hmac 交叉验证）。

    作用：用两个独立手段确认签名算法正确——①测试内用 hmac 标准库
          按同一算法独立重算一遍做交叉验证；②冻结实现首次跑通时的
          golden 值，防止「实现与参考实现一起漂移」的假绿。
    输入：假 secret + 固定待签名串。
    输出：实现结果 == 参考实现结果 == 冻结 golden 值。
    """
    secret = "fake_access_key_secret"
    string_to_sign = "POST&%2F&Action%3DTranslateGeneral%26Format%3DJSON"

    # 参考实现：与模块同算法（hmac-sha1 + base64），独立写在测试里
    digest = hmac.new(
        (secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")

    assert translator._compute_signature(secret, string_to_sign) == expected
    # 冻结 golden 值（2026-08-08 实现首次跑通时确定，勿改动）
    assert translator._compute_signature(secret, string_to_sign) == "RWl0w/2z+BSo2iTPkkCCHqL/Nzc="


# ---------------------------------------------------------------------------
# 请求 payload（3 条）
# ---------------------------------------------------------------------------

def test_payload_contains_required_params():
    """验证 _build_request_payload 包含全部必需参数。

    作用：确认公共参数（Action/Version/Format/AccessKeyId/签名方法/签名
          版本/SignatureNonce/Timestamp）与业务参数（FormatType/Scene/
          SourceLanguage/TargetLanguage/SourceText）齐全，且按决策 3
          不包含 Context 键。
    输入：一段假文本 + api 配置（假凭证）。
    输出：payload 各键存在且值正确；"Context" 不在 payload 中。
    """
    payload = translator._build_request_payload("السلام", _api_config(), "【术语约束】")

    # 公共参数
    assert payload["Action"] == "TranslateGeneral"
    assert payload["Version"] == "2018-10-12"
    assert payload["Format"] == "JSON"
    assert payload["AccessKeyId"] == "fake_access_key_id"
    assert payload["SignatureMethod"] == "HMAC-SHA1"
    assert payload["SignatureVersion"] == "1.0"
    # 业务参数
    assert payload["FormatType"] == "text"
    assert payload["Scene"] == "general"
    assert payload["SourceLanguage"] == "ar"
    assert payload["TargetLanguage"] == "zh"
    assert payload["SourceText"] == "السلام"
    # 签名键存在（非空）
    assert payload["Signature"]
    # 决策 3：约束参数位未启用，payload 不得出现 Context 键
    assert "Context" not in payload


def test_payload_nonce_differs_between_calls():
    """验证每次构造 payload 的 SignatureNonce 都不同且为合法 UUID。

    作用：确认防重放参数每次请求唯一——两次构造同一内容的 payload，
          随机串不能相同；格式必须是合法 UUID。
    输入：相同文本与配置调用两次 _build_request_payload。
    输出：两个 nonce 不相等，且都能解析为 UUID。
    """
    payload1 = translator._build_request_payload("مرحبا", _api_config(), "")
    payload2 = translator._build_request_payload("مرحبا", _api_config(), "")

    assert payload1["SignatureNonce"] != payload2["SignatureNonce"]
    # UUID(nonce)：nonce 不是合法 UUID 时抛 ValueError；
    # str(UUID(x)) == x 可校验格式无多余字符
    assert str(UUID(payload1["SignatureNonce"])) == payload1["SignatureNonce"]


def test_payload_timestamp_iso8601_utc_near_now():
    """验证 Timestamp 符合 ISO8601 UTC 格式且接近当前时间。

    作用：确认时间戳格式正确（形如 2026-08-08T12:00:00Z，结尾 Z 表示
          UTC），且与当前时间相差在容差内（阿里云对时间偏差敏感）。
    输入：调用 _build_request_payload 一次。
    输出：时间戳以 Z 结尾、可解析、与当前 UTC 时间差 < 5 分钟。
    """
    payload = translator._build_request_payload("نص", _api_config(), "")

    timestamp = payload["Timestamp"]
    # 结尾 Z 是 UTC 的固定标记（ISO8601）
    assert timestamp.endswith("Z")
    # 把 "2026-08-08T12:00:00Z" 转成带时区的 datetime：
    # 先 .replace("Z", "+00:00") 换成标准时区后缀，再 fromisoformat 解析
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    # 与当前 UTC 时间对比：偏差超过 300 秒说明格式/时区不对
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 300


# ---------------------------------------------------------------------------
# 双模式编排（5 条）
# ---------------------------------------------------------------------------

def test_mock_mode_makes_no_network_request(monkeypatch):
    """验证 mock 模式不发起任何网络请求。

    作用：确认 mock 模式的承诺——纯本地生成占位译文；若误发请求，
          假 POST 会抛 AssertionError 直接让测试失败。
    输入：TRANSLATION_ENGINE=mock（显式设定）；假 POST 见调用即抛。
    输出：2 段输入得到 2 条占位译文，且无人调用假 POST。
    """
    # 显式设定 mock 模式（防开发者 shell 环境变量干扰）
    monkeypatch.setenv("TRANSLATION_ENGINE", "mock")

    def fake_post(*args, **kwargs):
        # 只要被调用就抛错：mock 模式不允许出现任何网络请求
        raise AssertionError("mock 模式不应发起任何网络请求")

    # monkeypatch.setattr：把 translator.requests.post 换成假函数
    monkeypatch.setattr(translator.requests, "post", fake_post)

    translations = translator.translate_paragraphs(["第1段", "第2段"])
    # 占位文案与阶段 1 逐字节一致
    assert translations == [
        "（占位译文·第1段）待接入翻译 API",
        "（占位译文·第2段）待接入翻译 API",
    ]


def test_api_missing_keys_falls_back_to_mock(monkeypatch):
    """验证 api 模式缺两个 key 时回退占位译文且不联网。

    作用：确认「缺 key 不报错」决策——engine=api 但无凭证时，走
          _translate_with_mock 并返回占位译文，页面据此显示黄色提示。
    输入：TRANSLATION_ENGINE=api、凭证变量被清空；假 POST 见调用即抛。
    输出：译文为占位文案；假 POST 未被调用。
    """
    monkeypatch.setenv("TRANSLATION_ENGINE", "api")
    # 清掉可能存在的真实凭证，确保「无凭证」状态
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_SECRET", raising=False)

    def fake_post(*args, **kwargs):
        raise AssertionError("缺 key 回退场景不应发起任何网络请求")

    monkeypatch.setattr(translator.requests, "post", fake_post)

    translations = translator.translate_paragraphs(["阿语段落"])
    assert translations == ["（占位译文·第1段）待接入翻译 API"]


def test_api_single_key_falls_back_to_mock(monkeypatch):
    """验证 api 模式只配一个 key 时同样回退占位。

    作用：确认 has_credentials 要求「ID 与 Secret 都非空」——只配任一
          个都不算完整凭证，回退占位，避免拿着半个凭证去签名被拒。
    输入：两种情况——只配 ID / 只配 Secret。
    输出：两种情况下译文都是占位文案，且未发请求。
    """
    monkeypatch.setenv("TRANSLATION_ENGINE", "api")

    def fake_post(*args, **kwargs):
        raise AssertionError("只配一个 key 的回退场景不应发起请求")

    monkeypatch.setattr(translator.requests, "post", fake_post)

    # 情况 1：只有 AccessKey ID
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "fake_access_key_id")
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_SECRET", raising=False)
    assert translator.translate_paragraphs(["段落甲"]) == ["（占位译文·第1段）待接入翻译 API"]

    # 情况 2：只有 AccessKey Secret
    monkeypatch.delenv("ALIYUN_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "fake_access_key_secret")
    assert translator.translate_paragraphs(["段落乙"]) == ["（占位译文·第1段）待接入翻译 API"]


def test_api_translates_all_paragraphs_serially(monkeypatch):
    """验证 api 模式逐段串行翻译：3 段 → 3 次请求，内容与顺序正确。

    作用：确认 api 全链路（payload 构造 → POST → 解析）——每段各发
          一次请求，endpoint/Content-Type/timeout 正确，body 里的
          SourceText 与段落一一对应且顺序一致。
    输入：api 模式 + 假凭证；假 POST 返回固定成功响应并记录调用。
    输出：3 段全部得到译文；calls 记录 3 次调用且各项符合预期。
    """
    _set_api_env(monkeypatch)
    response = _FakeResponse(200, {"Code": "200", "Data": {"Translated": "（真译文）"}})
    fake_post, calls = _fixed_fake_post(response)
    monkeypatch.setattr(translator.requests, "post", fake_post)

    paragraphs = ["第1段", "第2段", "第3段"]
    translations = translator.translate_paragraphs(paragraphs)

    # 每段都返回固定成功响应的译文
    assert translations == ["（真译文）", "（真译文）", "（真译文）"]
    # 3 段 → 恰好 3 次请求（逐段串行）
    assert len(calls) == 3
    for call in calls:
        # 请求地址必须是默认服务地址
        assert call["url"] == settings.DEFAULT_ENDPOINT
        # Content-Type 必须是表单类型（body 是规范化查询串）
        assert call["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
        # 超时秒数来自配置（默认 30）
        assert call["timeout"] == settings.DEFAULT_TIMEOUT

    # body 是规范化查询串：用 parse_qs 解码后按序取出 SourceText
    for i, call in enumerate(calls):
        parsed = urllib.parse.parse_qs(call["data"])
        # parse_qs 自动做百分号解码：第 i 段文本应出现在第 i 次请求里
        assert parsed["SourceText"] == [paragraphs[i]]


def test_api_success_response_parsed(monkeypatch):
    """验证成功响应解析出译文文本。

    作用：确认 _parse_response 的正向路径——Code=="200" 且
          Data.Translated 存在时，返回译文本身。
    输入：api 模式 + 假凭证；成功响应含译文「译好的文本」。
    输出：translate_paragraphs 返回该译文。
    """
    _set_api_env(monkeypatch)
    response = _FakeResponse(200, {"Code": "200", "Data": {"Translated": "译好的文本"}})
    fake_post, _ = _fixed_fake_post(response)
    monkeypatch.setattr(translator.requests, "post", fake_post)

    translations = translator.translate_paragraphs(["نص عربي"])
    assert translations == ["译好的文本"]


# ---------------------------------------------------------------------------
# 术语约束（3 条）
# ---------------------------------------------------------------------------

def test_constraints_filtered_by_paragraph():
    """验证约束按段落过滤：只输出出现在该段的命中。

    作用：确认 build_translation_constraints 的过滤语义——给定段落号时
          按 hit["paragraphs"] 过滤；paragraph_no=None 时输出全部。
    输入：含多段命中的术语/专名列表。
    输出：段落号 2 只含段 2 的命中；None 含全部。
    """
    term_hits = [
        {"阿语原文": "فلسطين", "中文译文": "巴勒斯坦", "类别": "地名",
         "处理方式": "force_check", "paragraphs": [1, 3]},
        {"阿语原文": "السلام", "中文译文": "和平", "类别": "政治",
         "处理方式": "suggest", "paragraphs": [2]},
    ]
    name_hits = [
        {"阿语原文": "مصر", "中文译文": "埃及", "类别": "国名", "paragraphs": [1]},
    ]

    # 段落号 2：只应出现「和平」这一条术语，专名「埃及」不出现在段 2
    constraints = translator.build_translation_constraints(term_hits, name_hits, paragraph_no=2)
    assert "【术语约束】" in constraints
    assert "السلام" in constraints and "和平" in constraints
    assert "فلسطين" not in constraints
    assert "【专名约束】" not in constraints  # 段 2 无专名命中

    # 段落号 None：全部命中都输出（术语 2 条 + 专名 1 条）
    constraints_all = translator.build_translation_constraints(term_hits, name_hits)
    assert "فلسطين" in constraints_all and "巴勒斯坦" in constraints_all
    assert "السلام" in constraints_all
    assert "【专名约束】" in constraints_all and "مصر" in constraints_all


def test_constraints_no_hits_returns_empty():
    """验证无命中时返回空串。

    作用：确认约束文本的边界行为——命中列表为空/为 None、或过滤后
          无命中时，返回 ""（而不是报错或输出空标记）。
    输入：None 与空列表组合，以及「过滤后无命中」的情况。
    输出：全部返回 ""。
    """
    # 两个库都为 None
    assert translator.build_translation_constraints(None, None) == ""
    # 两个库都是空列表
    assert translator.build_translation_constraints([], []) == ""
    # 命中存在但都不在指定段落 → 过滤后为空
    hits = [{"阿语原文": "مصر", "中文译文": "埃及", "类别": "国名", "paragraphs": [3]}]
    assert translator.build_translation_constraints(hits, [], paragraph_no=1) == ""


def test_api_request_body_has_no_constraints_or_context(monkeypatch):
    """验证 api 模式请求 body 中不出现约束文本与 Context 键（决策 3）。

    作用：确认阶段 2 决策 3 落实到请求层——即使传入了术语命中，
          body 里也不得出现约束文本（术语约束未发送给阿里云）。
    输入：api 模式 + 假凭证；传入含独特标记的术语命中。
    输出：解码后的 body 无 Context 键、无约束文本；翻译照常成功。
    """
    _set_api_env(monkeypatch)
    response = _FakeResponse(200, {"Code": "200", "Data": {"Translated": "译文"}})
    fake_post, calls = _fixed_fake_post(response)
    monkeypatch.setattr(translator.requests, "post", fake_post)

    # 用独特标记词作词条，确保断言不会误中其他内容
    term_hits = [
        {"阿语原文": "标记词XYZ", "中文译文": "独特译文", "类别": "测试",
         "处理方式": "force_check", "paragraphs": [1]},
    ]
    translator.translate_paragraphs(["阿语原文"], term_hits=term_hits)

    assert len(calls) == 1
    body = calls[0]["data"]
    # 原始 body 里不允许出现 Context 键名
    assert "Context" not in body
    # 解码后逐值检查：约束文本与词条标记都不应出现在任何参数值里
    parsed = urllib.parse.parse_qs(body)
    assert "Context" not in parsed
    for values in parsed.values():
        for value in values:
            assert "术语约束" not in value
            assert "标记词XYZ" not in value


# ---------------------------------------------------------------------------
# 错误路径（6 条）
# ---------------------------------------------------------------------------

def test_business_error_carries_paragraph_no(monkeypatch):
    """验证业务错误（Code≠200）抛 TranslationBusinessError 且带段号。

    作用：确认业务失败的异常语义——HTTP 200 但阿里云返回错误码
          （如 InvalidAccessKeyId）时抛业务异常，携带出错段落号与
          阿里云错误码，供 UI 显示「第 N 段失败」。
    输入：api 模式；第 1 段成功、第 2 段返回业务错误。
    输出：抛 TranslationBusinessError，paragraph_no == 2，
          aliyun_code == "InvalidAccessKeyId"。
    """
    _set_api_env(monkeypatch)
    ok = _FakeResponse(200, {"Code": "200", "Data": {"Translated": "第一段译文"}})
    err = _FakeResponse(200, {"Code": "InvalidAccessKeyId", "Message": "凭证无效"})
    fake = _SequentialFakePost([ok, err])
    monkeypatch.setattr(translator.requests, "post", fake)

    with pytest.raises(translator.TranslationBusinessError) as exc_info:
        translator.translate_paragraphs(["第一段", "第二段", "第三段"])

    # 出错的是第 2 段（段落号 1 基）
    assert exc_info.value.paragraph_no == 2
    # 阿里云错误码随异常携带（页面排查用）
    assert exc_info.value.aliyun_code == "InvalidAccessKeyId"


def test_http_500_raises_network_error(monkeypatch):
    """验证 HTTP 状态码非 200 抛 TranslationNetworkError 且带段号。

    作用：确认网关错误的异常语义——HTTP 500 属网络层失败，不按
          业务错误处理，异常携带段落号。
    输入：api 模式；假响应 status_code=500。
    输出：抛 TranslationNetworkError，paragraph_no == 1。
    """
    _set_api_env(monkeypatch)
    response = _FakeResponse(status_code=500)
    fake_post, _ = _fixed_fake_post(response)
    monkeypatch.setattr(translator.requests, "post", fake_post)

    with pytest.raises(translator.TranslationNetworkError) as exc_info:
        translator.translate_paragraphs(["一段"])
    assert exc_info.value.paragraph_no == 1


def test_timeout_raises_network_error(monkeypatch):
    """验证请求超时抛 TranslationNetworkError。

    作用：确认超时的异常语义——requests 抛 Timeout 时转为网络异常，
          携带段落号，页面提示「第 N 段翻译请求超时」。
    输入：api 模式；假 POST 抛 requests.exceptions.Timeout。
    输出：抛 TranslationNetworkError，paragraph_no == 1。
    """
    _set_api_env(monkeypatch)

    def fake_post(*args, **kwargs):
        # 模拟超时：requests 在超时时抛 Timeout 异常
        raise translator.requests.exceptions.Timeout("连接超时")

    monkeypatch.setattr(translator.requests, "post", fake_post)

    with pytest.raises(translator.TranslationNetworkError) as exc_info:
        translator.translate_paragraphs(["一段"])
    assert exc_info.value.paragraph_no == 1


def test_connection_error_raises_network_error(monkeypatch):
    """验证连接失败抛 TranslationNetworkError。

    作用：确认连接失败的异常语义——DNS 解析失败/拒连等抛网络异常，
          携带段落号。
    输入：api 模式；假 POST 抛 requests.exceptions.ConnectionError。
    输出：抛 TranslationNetworkError，paragraph_no == 1。
    """
    _set_api_env(monkeypatch)

    def fake_post(*args, **kwargs):
        # 模拟连接失败：requests 在无法连接时抛 ConnectionError
        raise translator.requests.exceptions.ConnectionError("拒绝连接")

    monkeypatch.setattr(translator.requests, "post", fake_post)

    with pytest.raises(translator.TranslationNetworkError) as exc_info:
        translator.translate_paragraphs(["一段"])
    assert exc_info.value.paragraph_no == 1


def test_parse_errors_invalid_json_and_missing_field(monkeypatch):
    """验证解析失败抛 TranslationParseError：非 JSON 与缺 Data.Translated。

    作用：确认解析层的异常语义——响应体不是合法 JSON、或缺少
          Data.Translated 字段，都抛解析异常并携带段落号。
    输入：api 模式；三种坏响应——json() 抛 ValueError / 缺 Data /
          有 Data 但缺 Translated。
    输出：三种情况都抛 TranslationParseError，paragraph_no == 1。
    """
    _set_api_env(monkeypatch)

    # 情况 1：响应体不是合法 JSON（json() 抛 ValueError）
    bad_json = _FakeResponse(200, json_error=ValueError("不是 JSON"))
    fake_post, _ = _fixed_fake_post(bad_json)
    monkeypatch.setattr(translator.requests, "post", fake_post)
    with pytest.raises(translator.TranslationParseError) as exc_info:
        translator.translate_paragraphs(["一段"])
    assert exc_info.value.paragraph_no == 1

    # 情况 2：有 Code 但整个 Data 缺失
    no_data = _FakeResponse(200, {"Code": "200"})
    fake_post, _ = _fixed_fake_post(no_data)
    monkeypatch.setattr(translator.requests, "post", fake_post)
    with pytest.raises(translator.TranslationParseError):
        translator.translate_paragraphs(["一段"])

    # 情况 3：有 Data 但缺 Translated 字段
    no_field = _FakeResponse(200, {"Code": "200", "Data": {}})
    fake_post, _ = _fixed_fake_post(no_field)
    monkeypatch.setattr(translator.requests, "post", fake_post)
    with pytest.raises(translator.TranslationParseError):
        translator.translate_paragraphs(["一段"])


def test_fail_fast_stops_after_failed_paragraph(monkeypatch):
    """验证 fail-fast：第 2 段失败后第 3 段不再发请求。

    作用：确认「一段失败中断整批」决策——异常在 translate_paragraphs
          内部直接上抛，后续段落不再翻译（串行循环被打断）。
    输入：api 模式；第 1 段成功、第 2 段解析失败（缺 Translated）。
    输出：抛异常；请求总数恰好 2 次（第 3 段未发送）。
    """
    _set_api_env(monkeypatch)
    ok = _FakeResponse(200, {"Code": "200", "Data": {"Translated": "译文甲"}})
    bad = _FakeResponse(200, {"Code": "200", "Data": {}})  # 缺 Translated → 解析失败
    fake = _SequentialFakePost([ok, bad])
    monkeypatch.setattr(translator.requests, "post", fake)

    with pytest.raises(translator.TranslationParseError) as exc_info:
        translator.translate_paragraphs(["第一段", "第二段", "第三段"])

    assert exc_info.value.paragraph_no == 2
    # fail-fast：只有前 2 段发了请求，第 3 段没有发送
    assert len(fake.calls) == 2
