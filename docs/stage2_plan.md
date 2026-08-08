# 阶段 2 实施计划：translator.py 接入阿里云机器翻译（可配置双模式）

> 状态：**已批准并实施完成**（2026-08-08 批准；同日实施完毕，73 测试全绿，真实翻译联调通过）
> 对应计划文件：C:\Users\Lenovo\.claude\plans\python-encapsulated-noodle.md
> 实施纪要：docs/meeting_notes.md「2026-08-08 · 第 2 阶段需求确认与设计决策」

## Context（背景）

Tarjuman 项目第 1 阶段已完成（Streamlit 页面 + 纯本地流水线 + 47 个测试全绿）。第 2 阶段目标：**只把 modules/translator.py 的占位译文替换为可配置的真实翻译 API 调用**（阿里云机器翻译 TranslateGeneral）。不做 LLM 审校（阶段 3）、不做 SQLite（阶段 4）、不重构。

项目根目录：`d:\G\python_coding\proj_vibe_coding\arabic-review-mvp\`

## 已确认决策（用户拍板，必须遵守）

1. **调用方式**：requests 手写 RPC 签名（不引入阿里云 SDK）
2. **缺 Key（engine=api 但无 AK/Secret）**：回退占位译文 + 页面黄色提示，不报错中断
3. **术语约束**：`build_translation_constraints` 正常生成，但**暂不发送**给阿里云（已核实 TranslateGeneral 业务参数仅 Action/FormatType/Scene/SourceLanguage/TargetLanguage/SourceText，无 context/术语参数）；payload 构造函数预留参数位，注释说明未来接 LLM 时启用
4. **DeepSeek key**：仅写入 .env 预留 + .env.example 占位说明，本阶段不调用
5. 翻译方向 ar→zh，语言代码环境变量可配置；逐段串行，一次调用翻译一段
6. 一段失败 → **中断整批**，异常携带段落号，页面 st.error 显示（不整页崩溃）
7. 项目风格不变：纯函数原则、docstring「作用/输入/输出」、库方法调用前写注释、显式 UTF-8

## 文件改动清单

**新增 1 个**：`.env.example`（环境变量模板，只含占位说明，不填真实值）

**修改 5 个**：
| 文件 | 改动 |
|---|---|
| modules/translator.py | 核心重写：双模式（mock/api）+ 配置加载 + 约束生成 + RPC 签名客户端 |
| app.py | 顶部 `load_dotenv()`；run_pipeline 传 term_hits/name_hits；捕获 TranslationError/ValueError；缺 key 黄色提示；译文标签随模式变化；页面 caption 更新 |
| tests/test_translator.py | 保留 3 条阶段 1 测试（断言零改动）+ 新增约 25 条 |
| requirements.txt | 追加 `requests`、`python-dotenv` |
| CLAUDE.md / README.md / docs/meeting_notes.md / docs/modules.md | 阶段 2 同步更新（最后一步） |

**明确不动**：glossary.py、segmenter.py、reviewer.py、storage.py、data/、prompts/、.gitignore（已有 `.env` 条目）。

## 环境变量设计

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TRANSLATION_ENGINE` | `mock` | `mock`（占位不联网）/ `api`（调阿里云）；非法值抛 ValueError |
| `ALIYUN_ACCESS_KEY_ID` | 空串 | API 模式必填；缺 key 自动回退占位 |
| `ALIYUN_ACCESS_KEY_SECRET` | 空串 | 同上 |
| `ALIYUN_MT_ENDPOINT` | `https://mt.aliyuncs.com/` | 一般无需修改 |
| `TRANSLATION_SOURCE_LANG` | `ar` | 源语言代码 |
| `TRANSLATION_TARGET_LANG` | `zh` | 目标语言代码 |
| `TRANSLATION_SCENE` | `general` | 翻译场景 |
| `TRANSLATION_TIMEOUT_SECONDS` | `30` | 单请求超时，非法值抛 ValueError |
| `DEEPSEEK_API_KEY` | 空串 | 阶段 3 预留，本阶段不读取不调用 |

所有值读取后 `.strip()`。dotenv 加载只发生在 **app.py 顶部**（`load_dotenv(BASE_DIR / ".env")`）；modules/ 不 import dotenv，保证可测试性。CLI 直接调 run_pipeline 需自行 load_dotenv（README 注明）。

`.env.example` 骨架：注释说明每个变量用途；Key 行留空；注明「只放本文件，绝不提交 git」。

## translator.py 函数契约

模块级常量：`MOCK_ENGINE`/`API_ENGINE`、`DEFAULT_ENDPOINT`/`DEFAULT_SOURCE_LANG`/`DEFAULT_TARGET_LANG`/`DEFAULT_SCENE`/`DEFAULT_TIMEOUT`。

**异常体系（全部中文消息，绝不包含任何密钥）**：
- `TranslationError(Exception)`：基类，带 `paragraph_no`（1 基，批外为 None）
- `TranslationNetworkError`：连接失败/超时/HTTP 非 200
- `TranslationBusinessError`：HTTP 200 但 Code≠"200"（InvalidAccessKeyId / ServiceNotOpened / SignatureDoesNotMatch 等），附阿里云错误码
- `TranslationParseError`：非 JSON / 缺 Data.Translated

**函数契约**：
- `TranslationConfig`（frozen dataclass）：engine / access_key_id / access_key_secret / endpoint / source_lang / target_lang / scene / timeout_seconds；`has_credentials` property（AK 与 Secret 都非空）
- `load_translation_config() -> TranslationConfig`：模块内唯一读 os.environ 的函数；非法 engine/超时值抛 ValueError（中文消息）
- `build_translation_constraints(term_hits, name_hits, paragraph_no=None) -> str`：按 `paragraph_no in hit.get("paragraphs", [])` 过滤（None 取全部）；格式「【术语约束】- 阿语原文 → 中文译文（类别：…；处理方式：…）【专名约束】…」；无命中返回 `""`；处理方式用 CSV 原始值（不复制 UI 映射）
- `translate_paragraphs(paragraphs, term_hits=None, name_hits=None) -> list[str]`：入口。`engine=="mock"` 或 `engine=="api"` 且无凭证 → `_translate_with_mock`；否则逐段 `_translate_with_api`（每段先 `build_translation_constraints(..., paragraph_no=i+1)`，串行）。空输入 → `[]`（api 模式也不发请求）
- `_translate_with_mock(paragraphs) -> list[str]`：逐段 `f"（占位译文·第{i+1}段）待接入翻译 API"` —— **与阶段 1 逐字节一致**，保证旧断言零改动
- `_translate_with_api(paragraph, constraints, config, paragraph_no) -> str`：构造 payload → POST → 解析；异常全带 paragraph_no

**签名与请求构造（纯函数，可单测）**：
- `_percent_encode(text)`：`urllib.parse.quote(text, safe="-_.~")`（空格→%20；`/`→%2F；其余 %XX 大写）
- `_canonicalized_query_string(params)`：键按字典序排序，各自编码后 `k=v` 以 `&` 连接
- `_build_string_to_sign(method, canonical)`：`f"{method}&%2F&{_percent_encode(canonical)}"`（canonical 整体再编码一次 → `%252F` 特征，勿再编码 `/`）
- `_compute_signature(secret, string_to_sign)`：`base64(hmac-sha1(secret+"&", string_to_sign))`
- `_build_request_payload(source_text, config, constraints)`：公共参数（Action=TranslateGeneral、Version=2018-10-12、Format=JSON、AccessKeyId、SignatureMethod=HMAC-SHA1、SignatureVersion=1.0、SignatureNonce=uuid4、Timestamp=ISO8601 UTC）+ 业务参数（FormatType=text、Scene、SourceLanguage、TargetLanguage、SourceText）；**预留位**：`# params["Context"] = constraints` 注释（未来启用，注释说明签名会自动覆盖新参数）；签名先对不含 Signature 的参数排序编码 → 算签名 → 加回 params
- POST 发送：body 直接用 `_canonicalized_query_string(payload)`（字符串形式，保证「发送字节 == 签名字节」），显式 `headers={"Content-Type": "application/x-www-form-urlencoded"}`，`timeout=config.timeout_seconds`
- `_parse_response(response, paragraph_no) -> str`：`response.json()` 失败 → TranslationParseError；Code≠"200" → TranslationBusinessError（Message 进消息）；缺 Data.Translated → TranslationParseError

## 错误处理策略

| 失败类型 | 行为 | 页面表现 |
|---|---|---|
| 缺 key（api 无凭证） | 不抛，回退占位 | st.warning「已配置 API 模式但未配置密钥，本次使用占位译文」 |
| 连接失败/超时 | TranslationNetworkError | st.error（含段号），中断整批 |
| HTTP 非 200 | TranslationNetworkError | 同上 |
| Code≠"200" | TranslationBusinessError | 同上（含阿里云错误码，如 ServiceNotOpened） |
| 解析失败 | TranslationParseError | 同上 |
| 环境变量非法 | ValueError | st.error（配置错误） |

**run_pipeline 返回结构扩展**：新增两键 `"translation_mode": str`（"mock"/"api"）、`"translation_fallback": bool`（本次是否回退占位），页面据此渲染提示，避免 UI 重复推断配置。旧 5 键不变（兼容）。

**旧结果不丢**：`st.session_state["results"] = run_pipeline(...)` 异常时不执行赋值，页面保留上次成功结果。

## app.py 改动要点

- 顶部：`from dotenv import load_dotenv` + `load_dotenv(BASE_DIR / ".env")`（带注释）
- run_pipeline：`translate_paragraphs(paragraphs, term_hits, name_hits)`；返回 dict 增加 translation_mode/translation_fallback
- 按钮回调：捕获 `translator.TranslationError`（st.error，含段号）与 `ValueError`（st.error 配置错误）
- 渲染：fallback 为真时顶部 st.warning；译文标签「第 N 段（译文·占位）」在 api 非回退时改为「第 N 段（译文）」
- 页面 caption 更新为阶段 2 说明

## 测试计划（tests/test_translator.py，预计约 28 条）

**通用约定**：不 import app.py；所有 key 用假值（`monkeypatch.setenv`）；mock 请求用 `monkeypatch.setattr(translator.requests, "post", fake_post)` + 假响应对象（status_code / .json()）；凡「必须是 mock」的测试显式 setenv TRANSLATION_ENGINE=mock（防开发者 shell 环境干扰）；**所有测试不打真实网络、不出现真实 key**。

**保留 3 条**：长度一致 / 含「占位译文」标记 / 空输入（仅加显式 setenv TRANSLATION_ENGINE=mock）

**新增 ~25 条**：
- 配置（4）：默认值 / 读环境变量+strip / 非法 engine 抛 ValueError / 非法 timeout 抛 ValueError
- 签名纯函数（4）：percent_encode 规则（空格→%20、/→%2F、中文阿语 %XX）/ canonical 排序编码 golden / string_to_sign 格式（%252F 特征）/ 签名 known-answer（固定 Timestamp+Nonce+假 secret，实现时冻结 golden 值，测试内用 hmac 参考实现交叉验证）
- payload（3）：含全部必需参数 / SignatureNonce 两次不同 / Timestamp 符合 ISO8601 UTC 且接近当前时间
- 双模式（5）：mock 不联网 / api 缺 key 回退占位且不联网 / 只配一个 key 同样回退 / api 全段落翻译（fake_post 断言调 3 次、body/Content-Type/endpoint）/ 成功响应解析
- 约束（3）：按段落过滤 / 无命中返回 "" / **api 模式请求 body 中不出现约束文本与 Context 键**（验证决策 3）
- 错误路径（6）：业务错误带段号 / HTTP 500 / 超时 / 连接错误 / 非 JSON 与缺字段 / fail-fast（第 2 段失败后第 3 段未发送）

## 不做事项清单

1. 不引入阿里云 SDK；不发送术语约束给阿里云；不调用 DeepSeek
2. 不做 LLM 审校、SQLite、storage.py 改动；不重构 glossary/segmenter/reviewer/app.py 布局；不改 CSV 数据
3. 不做批量/并发请求、失败重试、流式输出（逐段串行、单次尝试）
4. 不做页面上引擎切换控件（模式由环境变量决定）
5. 不把任何 Key/Secret/Signature 写入代码、README、测试、日志、错误消息
6. 不引入 pandas/FastAPI/Dify/React/LangChain

## 实现顺序

1. requirements.txt 追加依赖 + 新建 .env.example
2. translator.py：a) 常量/异常/config/约束/mock（阶段 1 测试仍全绿）→ b) 签名四函数 + payload + _translate_with_api + 编排
3. 重写 test_translator.py，`python -m pytest -v` 全量跑（47 条旧 + ~25 条新）
4. app.py（load_dotenv、传 hits、捕获异常、fallback 提示、标签变化）
5. 手动冒烟：mock / api（真实 .env 真 key 验证翻译）/ api 缺 key 三种状态
6. 文档四件套更新

## 验证方式

1. `python -m pytest -v` 全绿（47 + 新增）
2. `python -c` 直接调 run_pipeline：mock 模式返回占位；api 模式（配真 key）返回真实译文；缺 key 返回占位 + translation_fallback=True
3. `streamlit run app.py`（**需重启 server，模块缓存问题**）：
   - mock：译文为占位，无警告
   - api + 真 key：译文为真实翻译（阿语 RTL 对照正常）
   - api 缺 key：译文占位 + 黄色警告
   - api + 假 key：st.error 显示阿里云业务错误（如 InvalidAccessKeyId），页面不崩，旧结果保留
4. 检查终端日志无任何密钥输出

## 安全注意事项

- 真实 AccessKey 与 DeepSeek key 仅写入本地 `.env`（gitignored），绝不进代码/README/测试/日志
- 用户提供的密钥已在聊天中暴露，联调验证通过后建议在阿里云/DeepSeek 控制台**轮换重置**
- 联调时若返回 ServiceNotOpened，需先在阿里云控制台开通「机器翻译」服务

---

## 待商榷问题（讨论区，随讨论更新）

- 2026-08-08 用户提出的两个概念问题已解答并采纳，未改动计划内容：
  1. **「mock 是什么意思」** → 假/模拟翻译引擎：不联网、返回占位字符串，用于开发测试与无密钥环境；默认模式，保证不配置也能跑（阶段 1 行为不变）。
  2. **「手动冒烟是什么意思」** → 冒烟测试 = 只验证核心路径走通的最基本检查（源自硬件「通电冒烟即坏」的比喻）；手动冒烟 = 不用 pytest，用真实环境（真 .env key、真网络）实际跑三种状态（mock / api 缺 key / api 真 key），验证自动化测试覆盖不到的真机链路。
- 实施中经冒烟发现并修复 1 个缺陷：空环境变量（模板留空）必须回落默认值而非报错（详见 meeting_notes.md 阶段 2 小节「开发中发现的问题」）。
