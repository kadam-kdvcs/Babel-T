# -*- coding: utf-8 -*-
"""翻译配置模块（阶段 2 重构：配置集中管理）。

模块作用
--------
集中管理翻译相关的全部配置：环境变量名、引擎标识、默认值、
配置对象 TranslationConfig 与配置加载函数 load_translation_config。

为什么单独一个文件
------------------
阶段 2 早期把配置与翻译逻辑混在 translator.py 里，文件同时含
「配置 + 类型定义 + 翻译逻辑」，修改不便。拆出本模块后：
- 改配置（环境变量名、默认值、校验规则）只动 settings.py 一个文件；
- translator.py 只保留翻译逻辑（约束构建、双模式编排、签名、请求）。

职责约定
--------
- 本模块是翻译相关配置的**唯一读取 os.environ 的地方**；translator.py
  只通过参数接收配置，保持纯函数特性、可测试；
- 模块内不 import python-dotenv：.env 的加载只发生在 app.py 顶部，
  命令行直接调用 run_pipeline 时需要自行 load_dotenv；
- 异常体系（TranslationError 家族）**刻意留在 translator.py**：错误是
  翻译行为的对外契约（app.py 用 except translator.TranslationError
  捕获），配置模块不混杂错误类型。

安全约定
--------
- 本模块不保存任何密钥字面量，只定义环境变量名；
- 密钥只允许存在于环境变量（.env）中，.env 已被 .gitignore 忽略。
"""

import os  # 标准库：读取环境变量（翻译配置的唯一读取处）
from dataclasses import dataclass  # 标准库：声明数据类（配置对象）


# ---------------------------------------------------------------------------
# 环境变量名（集中管理：将来改名只改这里，全项目同步生效）
# ---------------------------------------------------------------------------

# TRANSLATION_* 是翻译引擎通用配置；ALIYUN_* 是阿里云凭证与地址。
# 模板见 .env.example，真实值只填在本地 .env（gitignored）。
ENV_ENGINE = "TRANSLATION_ENGINE"              # 引擎模式：mock / api
ENV_ACCESS_KEY_ID = "ALIYUN_ACCESS_KEY_ID"     # 阿里云 AccessKey ID
ENV_ACCESS_KEY_SECRET = "ALIYUN_ACCESS_KEY_SECRET"  # 阿里云 AccessKey Secret
ENV_ENDPOINT = "ALIYUN_MT_ENDPOINT"            # 机器翻译服务地址
ENV_SOURCE_LANG = "TRANSLATION_SOURCE_LANG"    # 源语言代码（ar）
ENV_TARGET_LANG = "TRANSLATION_TARGET_LANG"    # 目标语言代码（zh）
ENV_SCENE = "TRANSLATION_SCENE"                # 翻译场景（general）
ENV_TIMEOUT_SECONDS = "TRANSLATION_TIMEOUT_SECONDS"  # 单请求超时（秒）
# ENV_DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"  # 阶段 3 LLM 审校预留，本阶段不读取


# ---------------------------------------------------------------------------
# 引擎标识与默认值
# ---------------------------------------------------------------------------

# 翻译引擎模式标识（TRANSLATION_ENGINE 的取值）
MOCK_ENGINE = "mock"  # mock：占位译文，不联网（默认）
API_ENGINE = "api"    # api：调用阿里云机器翻译接口

# 各环境变量的默认值（load_translation_config 在未设置/留空时使用）
DEFAULT_ENDPOINT = "https://mt.aliyuncs.com/"  # 阿里云机器翻译服务地址
DEFAULT_SOURCE_LANG = "ar"                     # 源语言：阿语
DEFAULT_TARGET_LANG = "zh"                     # 目标语言：中文
DEFAULT_SCENE = "general"                      # 翻译场景：通用
DEFAULT_TIMEOUT = 30                           # 单次请求超时（秒）


# ---------------------------------------------------------------------------
# 配置对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TranslationConfig:
    """翻译配置（不可变数据类）。

    作用：把散落的环境变量统一封装成一个对象，作为参数传给各翻译函数，
          使除 load_translation_config 外的所有函数保持纯函数特性
          （不直接读环境变量，只读传入的配置）。
    输入：engine —— "mock" 或 "api"；access_key_id / access_key_secret ——
          阿里云凭证（可能为空串）；endpoint —— 服务地址；
          source_lang / target_lang —— 源/目标语言代码；scene —— 场景；
          timeout_seconds —— 单次请求超时（秒）。
    输出：TranslationConfig 实例；frozen=True 表示实例不可修改
          （防止流水线中途被意外改掉配置）。
    """

    engine: str
    access_key_id: str
    access_key_secret: str
    endpoint: str
    source_lang: str
    target_lang: str
    scene: str
    timeout_seconds: int

    @property
    def has_credentials(self) -> bool:
        """是否已配置完整凭证（AccessKey ID 与 Secret 都非空）。

        作用：api 模式缺任一 key 时即视为「无凭证」，回退占位译文。
        输入：无（读自身字段）。
        输出：bool —— 两个凭证字段都非空为 True。
        """
        return bool(self.access_key_id) and bool(self.access_key_secret)


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_translation_config() -> TranslationConfig:
    """从环境变量加载翻译配置（翻译配置的唯一入口）。

    作用：读取 8 个 TRANSLATION_*/ALIYUN_* 环境变量并做校验与默认值
          填充，返回配置对象。所有值读取后 .strip()（防手误带空格）。
          DEEPSEEK_API_KEY 不读取（阶段 3 预留，本阶段不调用）。
    输入：无（隐式读取 os.environ）。
    输出：TranslationConfig —— 配置对象；engine 取值非法或
          timeout 非正整数时抛出 ValueError（中文消息）。
    """
    # os.environ.get(key, default)：读环境变量，未设置时返回 default。
    # .strip()：去掉首尾空白（Windows 环境变量编辑器常带入空格）。
    # 注意：.env 模板里变量留空（`KEY=`）时读到的是空串而非 default，
    # 所以这里统一「空串 = 未设置 = 用默认值」，保证复制 .env.example 后
    # 什么都不填也能按默认 mock 正常运行（与模板「留空即默认」一致）。
    engine_raw = os.environ.get(ENV_ENGINE, "").strip()
    engine = engine_raw or MOCK_ENGINE  # 空串（strip 后）→ 默认 mock
    # 引擎取值只允许 mock / api 两种，非法值立即抛错（配置错误，页面提示）
    if engine not in (MOCK_ENGINE, API_ENGINE):
        raise ValueError(
            f"环境变量 {ENV_ENGINE} 取值非法：{engine!r}，应为 {MOCK_ENGINE!r} 或 {API_ENGINE!r}"
        )

    # 超时必须是正整数：先尝试转 int，转不了或 ≤0 都算配置错误；
    # 空串同 engine 处理——视为未设置，用默认超时
    timeout_raw = os.environ.get(ENV_TIMEOUT_SECONDS, "").strip()
    timeout_raw = timeout_raw or str(DEFAULT_TIMEOUT)
    try:
        # int(str)：字符串转整数；转换失败会抛 ValueError
        timeout_seconds = int(timeout_raw)
    except ValueError:
        raise ValueError(
            f"环境变量 {ENV_TIMEOUT_SECONDS} 不是整数：{timeout_raw!r}"
        ) from None
    if timeout_seconds <= 0:
        raise ValueError(
            f"环境变量 {ENV_TIMEOUT_SECONDS} 必须为正整数"
        )

    return TranslationConfig(
        engine=engine,
        access_key_id=os.environ.get(ENV_ACCESS_KEY_ID, "").strip(),
        access_key_secret=os.environ.get(ENV_ACCESS_KEY_SECRET, "").strip(),
        # 其余字段同 engine 约定：空串视为未设置，回落默认值。
        # 注意 AK/SK 两项刻意不回落——空串正是「缺凭证 → 回退占位」的判定依据。
        endpoint=os.environ.get(ENV_ENDPOINT, "").strip() or DEFAULT_ENDPOINT,
        source_lang=os.environ.get(ENV_SOURCE_LANG, "").strip() or DEFAULT_SOURCE_LANG,
        target_lang=os.environ.get(ENV_TARGET_LANG, "").strip() or DEFAULT_TARGET_LANG,
        scene=os.environ.get(ENV_SCENE, "").strip() or DEFAULT_SCENE,
        timeout_seconds=timeout_seconds,
    )
