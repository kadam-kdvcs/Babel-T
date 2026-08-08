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

## 附：环境变量一览（模块 modules/settings.py:38-46）

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
