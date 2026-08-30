# 开发日志（Dev Log）

> 记录每个阶段改动了**哪些文件、哪些行、作用是什么**，供团队审查与新人接手。
> - **行号以当前仓库状态为准**（打开对应文件即可对照）；
> - 每个阶段末尾附该阶段的验证结果（测试数、冒烟情况）；
> - 与 docs/meeting_notes.md 的分工：meeting_notes 记「**为什么这样决策**」（决策日志），
>   dev_log 记「**改了什么**」（改动明细）。

---

## 阶段 1（2026-08-07）：纯本地流水线 MVP

**目标**：可运行的 Streamlit 页面 + 本地流水线（切分 → 词库扫描 → 占位译文 → 占位报告）+ 测试 + 文档。不接任何外部服务。

### 新增文件

| 文件 | 行号 | 内容与作用 |
|---|---|---|
| app.py | 全文（阶段 1 版 243 行；阶段 2 有改动，见后） | 唯一 UI 入口：页面布局、`run_pipeline(text)` 流水线编排、五个展示区渲染、RTL 样式与注入防护 |
| modules/segmenter.py | 全文 53 行 | `segment_paragraphs(text)`：有空行按空行分段、无空行按换行分段；纯函数 |
| modules/glossary.py | 全文 245 行 | 三个纯函数：`normalize_arabic`（:87，四项归一化）、`load_glossary`（:118，读 CSV）、`scan_glossary`（:169，子串匹配聚合） |
| modules/translator.py | 全文 29 行（占位） | `translate_paragraphs` 占位实现：返回「（占位译文·第N段）待接入翻译 API」 |
| modules/reviewer.py | 全文 41 行 | `generate_review_report`：占位审校报告，回填段落数与命中统计 |
| modules/storage.py | 全文 26 行 | 阶段 4 占位：仅文档说明 SQLite 表结构规划，不实现功能 |
| data/terms.csv | — | 术语库（六列，10 条示例），表头即字段名 |
| data/proper_names.csv | — | 专名库（四列，5 条示例） |
| data/samples/politics_001.txt | — | 示例阿语短文（5 段，故意混入变音符/派生词用于演示） |
| tests/test_segmenter.py | 全文 | 10 条测试：切分规则全边界（空/空白/无空行/空行/Windows 换行/段内换行） |
| tests/test_glossary.py | 全文 | 27 条测试：归一化 8 条（:61-123）、加载 7 条（:138-246）、扫描 12 条（:257-465） |
| tests/test_translator.py | 全文（3 条） | 占位译文长度一致（:151）、含标记（:168）、空输入（:185） |
| tests/test_reviewer.py | 全文 | 3 条测试：报告含统计、含占位标记、返回类型 |
| tests/test_data_integrity.py | 全文 | 4 条测试：两个 CSV 可加载且条数达标、样例切分段数、样例含变音符 |
| prompts/review_report_prompt.md | 全文 | LLM 审校提示词模板骨架（阶段 3 启用） |
| docs/meeting_notes.md | 全文 | 决策日志（阶段 1 需求确认与技术决策表） |
| docs/modules.md | 全文 | 模块功能说明（数据流图 + 各模块函数契约） |
| CLAUDE.md | 全文 | 给后续 Claude 会话的项目说明（技术栈/命令/目录/约定/规则） |
| README.md | 全文 | 用户文档（快速开始/数据格式/路线图） |
| requirements.txt | 全文 | 依赖：streamlit、pytest（最小集） |
| .gitignore | 全文 | 忽略 .venv/__pycache__/**.env** 等 |
| LICENSE | 全文 | 开源许可证 |

### 修改文件

无（git 仓库从零初始化，全部为新增）。

### 阶段 1 验证

- 47 条测试全绿（segmenter 10 + glossary 27 + translator 3 + reviewer 3 + data 4）
- 端到端：`streamlit run app.py` 页面可运行，示例文本切分/扫描/占位渲染正常
- 已合并 PR #1（commit d1a9674）

---

## 阶段 2（2026-08-08）：接入阿里云机器翻译（双模式）

**目标**：把占位译文替换为可配置的真实翻译 API（阿里云 TranslateGeneral，requests 手写 RPC 签名）。mock/api 双模式；缺 key 回退占位；异常中断整批并带段号；密钥只进 .env。

### 新增文件

| 文件 | 行号 | 内容与作用 |
|---|---|---|
| .env.example | 全文 39 行 | 9 个环境变量模板（含中文注释）；Key 行全部留空；注明「.env 已 gitignore，绝不提交」 |

### 修改文件

| 文件 | 行号 | 改动内容与作用 |
|---|---|---|
| modules/translator.py | 整体重写（29 → 584 行） | 占位实现替换为双模式：模块常量（原 :50-59）、异常体系（原 :66-114：TranslationError 基类带段号 + 网络/业务/解析三类子类）、TranslationConfig + load_translation_config（原 :121-206）、build_translation_constraints（原 :213-264，约束**生成不发送**）、translate_paragraphs 编排（原 :271-314，mock 或无凭证回退占位，api 逐段串行）、签名四函数（原 :334-404，percentEncode/字典序/%252F 双重编码/base64-hmac-sha1）、_build_request_payload（原 :411-463，预留 Context 注释位）、_translate_with_api（原 :479-538，body==签名字节）、_parse_response（原 :541-583）。重构后行号见下一节 |
| app.py | :22-27、:36-40 | 新增 python-dotenv 导入与 `load_dotenv(BASE_DIR / ".env")`：把 .env 密钥读入环境变量（唯一加载处） |
| app.py | :102 | `translate_paragraphs(paragraphs, term_hits, name_hits)`：传入命中供构建约束 |
| app.py | :104-111 | run_pipeline 读取配置，返回 dict 新增 `translation_mode` / `translation_fallback` 两键：页面据此渲染提示，不在 UI 层重复推断 |
| app.py | :166-167 | render_results 顶部：fallback 为真时 `st.warning`（「已配置 API 模式但未配置密钥…」） |
| app.py | :191-199 | 译文标签随模式变化：api 真实译文显示「第 N 段（译文）」，mock/回退显示「（译文·占位）」 |
| app.py | :231-235 | 页面 caption 更新为阶段 2 说明（双模式 + 回退行为） |
| app.py | :296-304 | 按钮回调新增异常捕获：`translator.TranslationError`（翻译失败，消息含段号）与 `ValueError`（配置错误）→ st.error；异常时不覆盖 session_state（旧结果保留） |
| tests/test_translator.py | 3 → 29 条（约 860 行） | 保留阶段 1 三条（:151/:168/:185，断言零改动，仅加显式 setenv mock）+ 新增 26 条：配置 4（:228-337，含空串回落默认）、签名 4（:339-423，known-answer 交叉验证）、payload 3（:425-495）、双模式 5（:497-626，含「api 请求 body 无约束文本与 Context 键」）、约束 3（:628-712）、错误路径 6（:714-859，含 fail-fast） |
| requirements.txt | :5-7 | 追加 `requests>=2.31`、`python-dotenv>=1.0`（各配中文注释） |
| CLAUDE.md | :9-10、:19-25、:46-49、:56-64 | 技术栈加入 requests/python-dotenv 与「阶段 2 已接翻译 API」；目录结构加 .env.example/.env；关键设计约定新增「翻译双模式」；当前阶段更新为阶段 2 决策 |
| README.md | :3、:5-33、:44-60、:72-78、:106-112 | 介绍更新为双模式；「当前阶段」改为阶段 2（双模式表 + 环境变量说明）；目录结构更新；快速开始加 .env 配置步骤；路线图阶段 1 勾选完成 |
| docs/meeting_notes.md | 末尾 | 追加「2026-08-08 · 第 2 阶段需求确认与设计决策」小节（需求确认表、开发中问题 4 条、验证结果） |
| docs/modules.md | :3-25、:65-95、:105-131 | 数据流图更新；translator 节重写为双模式（公开函数/异常体系/签名链/环境变量）；app.py 节更新 7 键返回与异常捕获 |
| docs/stage2_plan.md | :3、末尾 | 状态「待商榷」→「已批准并实施完成」；待商榷区记录 mock/冒烟两个概念问题解答与冒烟发现 bug |

### 阶段 2 开发中发现并修复的问题（均记录在 meeting_notes.md）

1. 空环境变量（.env 模板留空）原实现抛 ValueError 崩溃 → 改为「空串 = 未设置 = 默认值」
2. api 模式空输入契约补测试锁定（不发请求）
3. 真实网络联调一次通过（签名算法正确）

### 阶段 2 验证

- 73 条测试全绿（阶段 1 的 47 条零改动 + 新增 26 条）
- 手动冒烟三状态：mock 占位 / api 缺 key 回退+提示 / api 真 key 真实译文「欢迎来到阿拉伯项目」
- 安全核查：无密钥入代码/测试/日志；.env gitignored

---

## 阶段 2 收尾重构（2026-08-08）：配置拆分 modules/settings.py

**目标**：用户反馈 translator.py 混杂「配置 + 类型定义 + 翻译逻辑」繁琐难改 → 新建 settings.py 集中管理配置；异常体系留 translator（用户拍板）。

### 新增文件

| 文件 | 行号 | 内容与作用 |
|---|---|---|
| modules/settings.py | 全文 131 行 | 翻译配置中心：环境变量名常量（:38-46，`ENV_*`，改名只动一处）、引擎标识与默认值（:54-62，MOCK/API_ENGINE、DEFAULT_*）、TranslationConfig（:68-104，frozen dataclass + has_credentials）、load_translation_config（:110-131，唯一读 os.environ 处） |

### 修改文件

| 文件 | 行号 | 改动内容与作用 |
|---|---|---|
| modules/translator.py | :14-17（docstring） | 说明配置已移入 settings.py，本模块只通过参数接收配置 |
| modules/translator.py | :44-50 | 新增 `from modules.settings import MOCK_ENGINE, TranslationConfig, load_translation_config`（按需导入） |
| modules/translator.py | 删除原 :34-59 的 dataclass/os import 与常量区、原 :117-206 的 TranslationConfig 与 load_translation_config | 配置定义全部搬走；584 → 483 行。保留：异常体系（现 :57-105）、约束构建（现 :112-163）、双模式编排（现 :170-213）、签名链（现 :233-303）、payload（现 :310-362）、请求与解析（现 :378-482） |
| app.py | :33 | import 增加 settings：`from modules import glossary, reviewer, segmenter, settings, translator`（配置符号从 settings 取） |
| app.py | :109、:112 | `translator.load_translation_config()` → `settings.load_translation_config()`；`translator.API_ENGINE` → `settings.API_ENGINE` |
| app.py | :196 | render_results 译文标签判定 `translator.API_ENGINE` → `settings.API_ENGINE` |
| app.py | :298 | `except translator.TranslationError` 不变（异常刻意留 translator） |
| tests/test_translator.py | :33 | `from modules import settings, translator` |
| tests/test_translator.py | :115-145 | 辅助函数 `_api_config` / `_set_api_env` 改用 settings 符号（含 ENV_* 常量） |
| tests/test_translator.py | :228-337 | 配置类 4 条测试引用全部改 settings（含空串回落断言） |
| tests/test_translator.py | :594、:598 | 双模式断言 DEFAULT_ENDPOINT/DEFAULT_TIMEOUT 改 settings |
| CLAUDE.md | :30、:53-55 | 目录结构加 settings；关键设计约定新增「配置集中管理」（配置符号从 settings 取、异常留 translator） |
| docs/modules.md | :65-90 | 新增「modules/settings.py — 翻译配置中心」节；translator 节更新配置来源说明 |
| docs/meeting_notes.md | 末尾 | 追加「2026-08-08 · 阶段 2 收尾重构：配置拆分 settings.py」（决策表 + 经验记录） |

### 重构验证

- 73 条测试全绿（纯搬迁重构，行为零变化，无断言改动）
- 冒烟：.env=api 默认走真实翻译正常（「你好」）；api 真 key 正常（「欢迎来到项目」）
- 页面重启后 UI 正常

---

## 阶段 3（2026-08-09）：接入 LLM 生成审校报告（双模式 + 8 项报告结构）

**目标**：把占位审校报告升级为可配置的 LLM 审校报告（DeepSeek / OpenAI 兼容接口，requests 直调不引 SDK）。reviewer.py 双模式 mock/api；配置集中 settings.py；缺 key 回退占位；调用失败抛 ReviewError 家族；8 项报告结构模板；开发调试视图 radio。系统定位：API 翻译是不可省略的主步骤，LLM 是不可缺少的校准辅助（翻译 API 无法接入术语表，术语一致性靠 LLM 把关）。

### 新增文件

| 文件 | 行号 | 内容与作用 |
|---|---|---|
| docs/stage3_plan.md | 全文 | 阶段 3 实施计划（已批准并实施完成） |

### 修改文件

| 文件 | 行号 | 改动内容与作用 |
|---|---|---|
| modules/settings.py | :15-30（docstring）、:56-60、:80-82、:124-151、:212-263 | docstring 更新为「翻译与审校配置中心」；新增 5 个 ENV_* 常量（REVIEW_ENGINE/DEEPSEEK_API_KEY/DEEPSEEK_API_URL/REVIEW_MODEL/REVIEW_TIMEOUT_SECONDS，删除原 DEEPSEEK_API_KEY 预留注释行）；新增 3 个 DEFAULT_*（DEFAULT_DEEPSEEK_API_URL=base URL 无尾斜杠 / DEFAULT_REVIEW_MODEL=deepseek-chat / DEFAULT_REVIEW_TIMEOUT=30）；新增 ReviewConfig（frozen dataclass，has_credentials=bool(api_key)）；新增 load_review_config（与 load_translation_config 完全对称：空串回落/非法 engine 中文 ValueError/timeout 正整数校验/api_key 刻意不回落） |
| modules/reviewer.py | 整体重写（41 → 约 400 行） | 占位实现替换为双模式：异常家族（ReviewError → Network/Business/Parse，全中文不含密钥）、generate_review_report 编排（签名不变；空输入短路 api 不发请求；mock/缺 key → _generate_mock_report 占位文案与阶段 1 逐字节一致）、_load_prompt_template（UTF-8 读取；缺文件 FileNotFoundError；校验「## 系统消息」「## 用户消息」标记与用户段 4 占位符各恰好 1 次）、_format_hits_text（两库 .get 兼容；「【术语命中】共 N 条…」格式；无命中输出「无」）、_format_numbered（「第N段：」编号）、_render_user_message（split 后 str.replace 替换；系统段截到「## 系统消息」后）、_build_api_url（rstrip("/")+"/chat/completions"）、_build_request_payload（model+messages 双角色+temperature=0.3）、_review_with_api（编排+POST Bearer）、_parse_review_response（六类错误分类） |
| prompts/review_report_prompt.md | 整体重写 | 结构：标题+说明 →「## 系统消息」（角色设定 + 8 项输出结构每项带指示 + 3 条约束：不重翻全文/不添加原文没有的信息/术语以术语库为准含处理方式语义）→「## 用户消息」（数据说明 + 4 占位符各恰好 1 次） |
| app.py | :1-15（docstring）、:33、:100-115（run_pipeline 9 键）、:116-126（return）、:158-165（render_results 签名与 docstring）、:166-168（fallback 提示）、:168-183（show_translations 跳过双语对照）、:227-240（报告区三分支）、:295-307（except 链加 ReviewError）、:338-355（radio+调用）、:374-381（caption） | 模块 docstring 与 caption 更新阶段 3；run_pipeline 追加 review_config/review_mode/review_fallback（与翻译配置块对称），返回 9 键；except 链新增 reviewer.ReviewError（夹在 TranslationError 与 ValueError 之间）；新增 VIEW_REPORT_ONLY/VIEW_REPORT_WITH_TRANSLATIONS 常量与 st.radio（index=1 默认全量、key="review_view"、教学注释）+ caption「翻译 API 是不可省略的步骤…」；render_results 加 show_translations 参数（False 时跳过双语对照整节，术语/专名表仍显示）；报告区三分支（fallback→st.warning / api 真报告→st.markdown 不传 unsafe_allow_html+caption / mock→st.info） |
| tests/test_reviewer.py | 旧 3 条零改动 + 新增 26 条 | 配置 6（默认值/读环境+strip/非法 engine+空串回落/timeout 非法+空串回落/URL 空串回落+api_key 不回落）、双模式 4（mock 不联网/api 缺 key 回退逐字节相同/api 空输入不发请求/api 成功 1 次 post）、模板 4（替换正常无残留/缺文件/缺占位符/缺标记）、hits 格式化 3（术语六列/专名四列不抛 KeyError/全空「无」）、请求构造 3（URL 去尾斜杠/body 结构含不含 key 安全断言/headers+timeout）、错误路径 6（401→Business 不含 key/超时/连接/非 JSON/缺 choices/缺 content）；辅助 _FakeResponse（含 text）/ _fixed_fake_post / _set_api_review_env / _write_template 模板 fixture |
| .env.example | :36-48 | 原 DEEPSEEK_API_KEY 预留注释转正式（含安全提醒）；追加 REVIEW_ENGINE / DEEPSEEK_API_URL（注明 base URL 自动拼 /chat/completions）/ REVIEW_MODEL / REVIEW_TIMEOUT_SECONDS（9 → 13 个变量） |
| CLAUDE.md | :5、:9-10、:34、:53-54、:65-72 | 项目定位更新（翻译+LLM 审校）；技术栈「阶段 3 已接 LLM 审校（DeepSeek OpenAI 兼容、requests 直调、双模式 mock/api）」；目录 prompts 注释「阶段 3 已启用」；关键设计约定新增「审校双模式（阶段 3）」（LLM 配置集中 settings、异常留 reviewer、URL 语义、temperature 固定、LLM 定位校准辅助、radio 只影响展示）；当前阶段决策更新为阶段 3 |
| README.md | :3、:5-50、:64-72、:120-127 | 介绍与当前阶段更新为阶段 3；新增「审校双模式」表与 LLM 定位说明；「本阶段明确不做」更新（LLM 审校移除、加不让 LLM 重翻全文等）；目录结构 reviewer/prompts/docs 注释更新；快速开始补 REVIEW_ENGINE/DEEPSEEK_API_KEY 配置；路线图阶段 2 勾选完成、阶段 3 当前 |
| docs/modules.md | :18-19（数据流图）、:65-91（settings 节）、:117-139（reviewer 节重写）、:147-151、:160-165（app 节）、:178（tests 表） | 数据流图 reviewer 支路加 LLM 模板；settings 节改「翻译与审校配置中心」加 ReviewConfig/load_review_config；reviewer 节整体重写（双模式编排/异常家族/私有函数/模板文件说明）；app 节 run_pipeline 9 键、except 链 4 类、radio 与报告区三分支；tests 表 test_reviewer 更新 26 条说明 |
| docs/meeting_notes.md | 末尾 | 追加「2026-08-09 · 第 3 阶段需求确认与设计决策」（需求确认表 10 项 + Evaluator 反馈问题 2 条 + 验证结果） |

### 阶段 3 开发中发现并修复的问题（Evaluator 反馈）

1. 占位符校验从「全模板」强化为「用户段」（防止占位符误挪进系统消息段时残留字面量）
2. 模板头部说明（标题/维护者 blockquote）不再随系统消息发送（截到「## 系统消息」标记之后）
3. 计划文件 .env.example 计数笔误「9 → 14」实为「9 → 13」，已同步修正

### 阶段 3 验证

- 99 条测试全绿（阶段 1/2 的 73 条零改动 + 新增 26 条）
- 手动冒烟四状态：mock 占位 / api 缺 key 回退+黄条 / api 真 key 8 项报告 / api 假 key st.error「审校失败」旧结果保留；radio 两种视图切换正常
- 安全核查：无密钥入代码/测试/文档/异常消息；业务错误片段截断 200 字符；st.markdown 不传 unsafe_allow_html

---

## 阶段 3.1（2026-08-24）：LLM 纠正后译文 + 两种翻译结果展示

**目标**：用户反馈当前「仅 LLM 校准（审校报告）/ LLM 校准 + API 翻译结果」两个选项不符合预期，应为「显示 LLM 纠正过后的翻译 / 显示原始 API 翻译 + LLM 纠正过后的翻译结果（两者都显示）」。
原代码只生成 8 项审校报告，并未生成真正可展示的“LLM 纠正后译文”。
本次改动让 LLM 一次调用同时输出「逐段纠正后译文 + 审校报告」，并保留审校报告作为辅助展示。

### 新增/主要改动文件

| 文件 | 行号（当前） | 改动内容与作用 |
|---|---|---|
| modules/reviewer.py | :37、:71-73 | 新增 `import re`；新增 `_CORRECTED_SECTION_MARKER = "## 纠正后译文"` 与 `_REPORT_SECTION_MARKER = "## 审校报告"` 两个输出结构标记 |
| modules/reviewer.py | :134-264 | `generate_review_report` 改为兼容薄包装（调用 bundle 后只取 `report`）；新增 `generate_review_bundle`（一次调用返回 `{"report", "corrected_translations"}`）、`_generate_mock_bundle`（占位报告 + 占位纠正译文）、`_parse_review_bundle`（按两个二级标题拆 LLM 回复，未按结构输出时回退 `corrected_translations=原译文`、`report=整段文本`）、`_parse_numbered_translations`（从「第N段：…」解析纠正译文；条数不符时回退原译文） |
| prompts/review_report_prompt.md | 全文 | 提示词改为先输出 `## 纠正后译文`（逐段「第N段：纠正后译文」），再输出 `## 审校报告`（8 项）；约束由“绝不重翻全文”改为“在给定译文基础上修正，不脱离原文另译” |
| app.py | :7、:19、:72-73 | 模块 docstring 更新为阶段 3.1；radio 常量改为 `VIEW_CORRECTED_ONLY` / `VIEW_RAW_AND_CORRECTED` |
| app.py | :101、:143、:157 | `run_pipeline` 返回 dict 新增 `corrected_translations`，调用 `generate_review_bundle` 同时取 `report` 与 `corrected_translations` |
| app.py | :197-268 | `render_results(results, show_corrected_only=False)`：翻译对照区改为展示“原始 API 译文（可选）+ LLM 纠正后译文”；`show_corrected_only=True` 时只显示纠正译文，`False` 时两者都显示；兼容旧 session_state 缺 `corrected_translations` 时回退原始译文；审校报告仍保留 |
| app.py | :289、:397-421 | 去掉审校报告区的重复 fallback warning；radio 文案/说明改为“翻译结果展示”；渲染调用改为 `show_corrected_only=(view_mode == VIEW_CORRECTED_ONLY)` |
| tests/test_reviewer.py | :823-925 | 新增 4 条：结果包 mock 占位、api 解析纠正译文与报告、无结构标记安全回退、纠正段数不符回退 |
| README.md | 当前阶段/审校双模式/展示说明 | 阶段改为 3.1；说明 LLM 同时输出纠正译文 + 审校报告；radio 文案同步 |
| CLAUDE.md | 关键设计约定/当前阶段 | 补充 `generate_review_bundle` / `corrected_translations` / 两种展示视图 / 并发翻译；测试数更新为 105 |
| docs/modules.md | reviewer 节、app 节、tests 表 | reviewer 增加 bundle 函数与解析回退说明；app 增加 `show_corrected_only`、10 键返回结构、两种展示视图；测试表增加 4 条 |
| docs/meeting_notes.md | 末尾 | 追加「2026-08-24 · 第 3.1 阶段确认：翻译结果两种展示选项」决策记录 |
| .gitignore | :10-13 | 追加本地 pytest 临时目录忽略规则（`pytest_tmp*/`、`pt_*/`、`tmp[0-9]+/`），避免测试残留目录入库 |
| app.py | :319-323 | 页面顶部 caption 由「第 3 阶段」更新为「第 3.1 阶段」，补充 LLM 纠正译文与审校说明（Playwright 测试发现文案不一致后修复） |
| docs/test_report_2026-08-24.md | 全文新增 | Playwright UI 测试报告：真实 API/LLM 模式、Mock 模式、两种展示视图、空输入校验；测试结果通过 |
| modules/translator.py | :40-42、:54-57、:222-286 | 新增 `ThreadPoolExecutor` / `as_completed` / `Callable` 导入；新增 `_DEFAULT_MAX_WORKERS = 4`；新增 `translate_paragraphs_parallel(...)` 并发翻译入口（api 模式多段同时请求、主线程回调 on_translation、失败取消未开始请求） |
| app.py | :90-180、:409-470 | `run_pipeline` 改为委托 `run_pipeline_progressive`；新增渐进式流水线（on_paragraphs / on_translation / on_review_start / on_review_done）；按钮点击使用 st.empty 占位符动态显示“原文立即显示→译文逐个补充→LLM 审校中”，LLM 返回后渲染完整结果 |
| tests/test_translator.py | 新增 2 条 | `test_api_parallel_translates_all_paragraphs_and_calls_back`、`test_api_parallel_mock_mode_makes_no_network_request` |

### 阶段 3.1 开发/验证结果

- `python -m py_compile app.py modules/reviewer.py tests/test_reviewer.py` 通过
- 全量 `python -m pytest -q`：**105 passed**（103 条原有 + 2 条并发翻译新增）
- `run_pipeline` mock 冒烟返回 `corrected_translations`，且长度与段落数一致
- 页面两种视图：
  - 仅 LLM 纠正后译文
  - 原始 API 译文 + LLM 纠正后译文（默认）
  - 审校报告始终保留在下方
- Playwright 动态渲染验证通过：原文立即显示 → 译文逐个补充 → LLM 审校中 → 完整结果

---

## 阶段 3.2（2026-08-24）：LLM 直接翻译 / 修正 / 最终仲裁 + 四结果对比

**目标**：用户要求 LLM 不能只“修正 API 译文”，而应该执行完整多轮流程：
1. 直接翻译原文，得到一个结果；
2. 基于 API 译文进行修正，得到第二个结果；
3. 将两个结果比对并继续结合原文，仲裁出最终结果；
4. 最终仲裁以原文为最高依据，禁止添加原文未有的信息；
5. 页面将四个结果全部展示出来。

**当前实现**：改为**三次独立 LLM 调用**，各阶段使用独立提示词模板，避免上下文污染：
1. 直接翻译：只提供原文 + 词库命中，不提供 API 译文；
2. 修正：提供原文 + API 译文 + 词库命中；
3. 最终仲裁：提供原文 + 直接翻译 + 修正结果 + 词库命中，不提供 API 译文；输出最终结果 + 翻译取舍说明 + 8 项审校报告。

### 新增/主要改动

| 文件 | 行号（当前） | 改动内容与作用 |
|---|---|---|
| modules/reviewer.py | :55-85、:163-247、:258-330 | 新增三个提示词路径常量与各自占位符集合；`generate_review_bundle` 分三次调用 `_call_llm`；`_generate_mock_bundle`/`_parse_review_bundle` 增加 `tradeoff_notes`；`_parse_review_bundle` 只解析最终仲裁回复（最终结果/取舍说明/审校报告） |
| modules/reviewer.py | :560-620 | `_review_with_api` 替换为通用 `_call_llm(config, prompt_path, placeholders, replacements)`，复用一次请求完整流程 |
| prompts/direct_translation_prompt.md | 新增 | 只负责直接翻译；用户消息只有原文 + 词库命中 |
| prompts/correct_translation_prompt.md | 新增 | 只负责修正；用户消息为原文 + API 译文 + 词库命中 |
| prompts/review_report_prompt.md | 全文 | 最终仲裁：输出 `## 最终结果` / `## 翻译取舍说明` / `## 审校报告`；用户消息无 API 译文 |
| app.py | :177-208、:240-345 | 返回结构新增 `tradeoff_notes`（13 键）；`render_results` 增加“翻译取舍说明”展示区 |
| app.py | :500-520 | LLM 阶段提示改为“正在请求 LLM 多轮处理（直接翻译/修正/最终仲裁/审校）…” |
| modules/reviewer.py / modules/reviewer_parsing.py | 全部 | 新增降级标志：`direct_degraded` / `corrected_degraded` / `final_degraded` / `tradeoff_degraded`；解析失败回退时页面明确标注“已降级到 API 译文/修正结果”，避免把 API 译文伪装成 LLM 成功结果 |
| pipeline.py / streamlit_ui.py | 新增 | 把 app.py 的流水线编排拆到 pipeline.py，把结果渲染拆到 streamlit_ui.py，降低 app.py 体积 |
| tests/test_reviewer.py | :102-180、:422-475、:890-970 | `_write_template` 改为写三份模板；api 成功/占位符/请求头测试改为 3 次调用；四结果测试改为三段独立响应 |
| README.md / CLAUDE.md / docs/modules.md | 当前阶段/设计约定/模块说明 | 同步为阶段 3.2 三次独立调用 + 翻译取舍说明 + 降级标注 + 拆分说明 |

### 阶段 3.2 验证

- 全量 `python -m pytest -q`：**105 passed**
- Playwright 真实 API 模式验证：
  - 页面出现「翻译结果对比（四个结果）」
  - 每段显示四个标签：原始 API 译文 / LLM 直接翻译结果 / LLM 修正结果 / LLM 最终结果（以原文为准）
  - 浏览器控制台无 error / warning
- `docs/test_report_2026-08-24.md` 已补充四结果展示验证记录

---

## 远期备选（当前暂缓）：Web 前后端分离重构

> 用户明确指示：**当前先不推进前端重构计划**。此节仅作为远期技术备选记录，不作为下一步执行计划。
> 当前阶段仍以 Streamlit 版本为主。

**目标（若未来启动）**：保留当前 Streamlit 版本作保底，同时按「Django + DRF 后端 API + TypeScript 前端」路线搭建新的可拓展主界面。
前端框架暂不锁定（Vue 3 / React 后续对比后再定），因此第一阶段先做与框架无关的后端 API 和前端骨架。

### 若启动时的待办清单（按依赖顺序）

| 阶段 | 任务 | 说明 |
|---|---|---|
| 0 | 业务层整理 | 给 `modules/` 加 `__init__.py`；把 `run_pipeline_progressive` 的核心逻辑抽到独立 service（如 `services/pipeline_service.py`），让 Streamlit 和 Django 共用 |
| 1 | Django + DRF API | 新建 Django 项目与 `POST /api/v1/translate/`，复用 `modules/`；翻译异常映射为统一 JSON 错误；保留现有 pytest |
| 2 | Vite + TS 骨架 | 新建前端目录；先做框架无关页面（原文输入、按钮、结果展示、两种翻译视图）；Vite dev proxy 转发 `/api` 到 Django |
| 3 | 前端框架落地 | 对比 Vue 3 / React 后二选一；把骨架组件化 |
| 4 | 功能对齐 | 与 Streamlit 逐项对比：mock、api 缺 key、api 真 key、两种视图、动态展示、异常提示 |
| 5 | 后续拓展 | 数据库保存审校记录、登录权限、术语库管理、导出 PDF/Word |

### 关键约束

- Streamlit `app.py` 保持不动或仅做小幅兼容，始终可作为保底版本。
- `.env` 密钥只允许后端读取，前端绝不接触密钥。
- 前端框架没定之前，不写 Vue/React 专有组件，只写 API 类型和调用层。
- 当前 105 条 pytest 必须继续全绿；新增 Django/API 测试后总数继续增长。

---

## 附：环境变量一览（模块 modules/settings.py）

| 变量 | 默认值 | 用途 |
|---|---|---|
| TRANSLATION_ENGINE | mock | 翻译引擎模式：mock 占位 / api 真实翻译 |
| ALIYUN_ACCESS_KEY_ID | 空 | 阿里云凭证（api 必需，缺了回退占位） |
| ALIYUN_ACCESS_KEY_SECRET | 空 | 阿里云凭证（同上） |
| ALIYUN_MT_ENDPOINT | https://mt.aliyuncs.com/ | 翻译服务地址 |
| TRANSLATION_SOURCE_LANG | ar | 源语言 |
| TRANSLATION_TARGET_LANG | zh | 目标语言 |
| TRANSLATION_SCENE | general | 翻译场景 |
| TRANSLATION_TIMEOUT_SECONDS | 30 | 翻译单请求超时 |
| REVIEW_ENGINE | mock | 审校引擎模式：mock 占位 / api LLM 审校（阶段 3 新增） |
| DEEPSEEK_API_KEY | 空 | DeepSeek 凭证（api 必需，缺了回退占位；阶段 3 转正式） |
| DEEPSEEK_API_URL | https://api.deepseek.com | LLM base URL，自动拼 /chat/completions（阶段 3 新增） |
| REVIEW_MODEL | deepseek-chat | LLM 审校模型名（阶段 3 新增） |
| REVIEW_TIMEOUT_SECONDS | 30 | LLM 审校单请求超时（阶段 3 新增） |

| 变量 | 默认值 | 用途 |
|---|---|---|
| TRANSLATION_ENGINE | mock | 引擎模式：mock 占位 / api 真实翻译 |
| ALIYUN_ACCESS_KEY_ID | 空 | 阿里云凭证（api 必需，缺了回退占位） |
| ALIYUN_ACCESS_KEY_SECRET | 空 | 阿里云凭证（同上） |
| ALIYUN_MT_ENDPOINT | https://mt.aliyuncs.com/ | 服务地址 |
| TRANSLATION_SOURCE_LANG | ar | 源语言 |
| TRANSLATION_TARGET_LANG | zh | 目标语言 |
| TRANSLATION_SCENE | general | 翻译场景 |
| TRANSLATION_TIMEOUT_SECONDS | 30 | 单请求超时 |
| DEEPSEEK_API_KEY | 空（预留） | 阶段 3 LLM 审校，本阶段不读取 |
