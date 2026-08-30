# 开发会议记录（决策日志）

> 本文件记录每次开发会议的需求确认与技术决策，供团队回顾与新人接手。

## 2026-08-07 · 第 1 阶段需求确认与设计决策

### 一、需求确认（与用户逐项确认）

| 决策点 | 结论 |
|---|---|
| 技术栈 | Python + Streamlit + 标准库 csv；不 import pandas；暂不接翻译 API / LLM / SQLite |
| 段落切分 | 有空行按空行分段；全文无空行则按换行分段（每行一段） |
| terms.csv 结构 | 六列：阿语原文,中文译文,类别,领域,备注,处理方式（force_check 强检查 / suggest 推荐检查 / context_warning 语境检查） |
| proper_names.csv 结构 | 四列：阿语原文,中文译文,类别,备注 |
| 示例数据 | 预填：术语 10 条、专名 5 条、politics_001.txt 五段阿语短文 |
| 阿语归一化 | 仅四项：去变音符、去 tatweel、去 bidi 控制符、أإآٱ→ا |
| 匹配方式 | 归一化后纯子串匹配，无词边界判断 |
| 测试 | 追加 tests/ + pytest |
| 文档 | 本阶段产出 CLAUDE.md 与 docs/modules.md（模块说明，供审查） |

### 二、技术决策

1. **纯函数原则**：modules/ 下函数无 UI、无全局状态、路径作为参数传入；UI 全部收拢在 app.py；`run_pipeline(text)` 为 app.py 顶层普通函数，可命令行直接调用。好处：每个函数可独立测试，调试只看函数内部。
2. **CSV 键名 = 表头名**：两库表头不同（六列 vs 四列），加载后 dict 的键直接用表头中文名，一套加载/扫描函数共用，无需两套列映射。
3. **编码约定**：所有 CSV 用 utf-8-sig（Excel 兼容）；csv 模块打开文件必须 newline=""（Windows 下防止字段尾带 \r）；所有 open() 显式指定编码（Windows 默认 GBK）。
4. **归一化范围取舍**：不做 ى→ي、ة→ه（防误配：如 سنة/سنه）；不做 ؤ/ئ/ء（中位 hamza 是词义一部分，如 مؤتمر 会议、رئيس 总统）。字符集集中在 glossary.py 常量，增删必须同步更新测试。
5. **纯子串匹配（已知取舍）**：阶段 1 不做词边界、虚词前缀、词尾接缀规则，会命中派生词（如词条 فلسطين 命中正文 الفلسطينية）。这是「先搭框架、避免复杂化」的决策；后续阶段需要时再加边界规则（可参考的平衡点：前置虚词 و ف ب ك ل 与定冠词不阻断、词尾接缀阻断）。
6. **RTL 显示**：CSS 独立 class `.ar-para`（direction: rtl; unicode-bidi: embed），只影响阿语区域；用户文本先 html.escape 再拼 HTML 防止注入。
7. **session_state 最小用法**：只用一个键 "results" 存流水线结果，防止按钮点击后重跑丢失；改文本不点按钮时结果区保持旧结果（可接受行为）。
8. **扫描结果结构**：每条命中 = 词条原字段 + paragraph_no（1 基）+ count（该段出现次数）；不返回字符位置（阶段 1 从简，后续需要高亮时再加）。

### 三、示例数据设计意图

- politics_001.txt 五段短文故意混入：变音符（افتُتِحت）、闪音（أكّدت、السَّلام）、tanwin（بياناً）、派生词（الفلسطينية）——用于演示归一化命中与纯子串行为。
- 词条与专名全部人工核对过阿语拼写。

### 四、开发中发现的问题记录

1. **阿语定冠词 alif 省略（orthographic limitation）**：介词 لِ 与定冠词 ال 合并时按拼写规则省略 alif（لِلدولة → للدولة、لِلناس → للناس），因此词条「الدولة」（带 alif）不会命中正文「للدولة」（不带 alif）。这是纯子串匹配的固有边界，非代码 bug；若后续需要此类命中，需引入词形规则（如把词条拆成 دولة 词根）。
2. **命中聚合粒度调整**（用户提出）：原实现按（词条 × 段落）分组，同一词条跨段出现会拆成多行、每行次数都是 1，用户反馈「有重复词」。已改为**按词条聚合**：一行一词条，出现次数 = 全文累计，段落列列出全部出现段落（如「1, 4, 5」）；报告口径同步改为「去重条数 + 累计次数」（如「术语命中 8 条（共 14 次）」）。对应字段：paragraph_no（int）→ paragraphs（list[int]）。

### 五、待定事项（后续阶段处理）

- 阶段 2：翻译 API 选型（届时新增 .env.example）
- 阶段 3：审校报告格式与 LLM 选型（prompts/review_report_prompt.md 已预写模板骨架）
- 后续：是否引入词边界规则、是否需要语料库支持

## 2026-08-08 · 设计讨论：scan_glossary 归一化归属

### 一、讨论背景

用户提出设计疑问：modules/glossary.py 的 `scan_glossary` 在内部直接调用 `normalize_arabic`（归一化每个词条与每个段落），按单一职责原则（SRP），归一化是否应该挪到 app.py？

### 二、讨论结论（用户拍板：保持现状）

| 观点 | 结论 |
|---|---|
| 观察是否成立 | 成立——scan_glossary 内部确实做了「归一化 + 匹配」两段操作 |
| 是否移到 app.py | **否**。理由：1) 归一化是匹配的内部准备步骤，scan 的单一职责是「产出命中报告」，归一化与子串匹配强耦合（匹配正确性依赖归一化）；2) 移到 app.py 违反**分层职责**——app.py 是 UI 层，掺入文本处理编排与纯函数原则冲突；3) 拆开会产生「输入必须已归一化」的隐藏前置条件，调用方与测试都易踩坑 |
| 现状是否已满足 SRP | 是——`normalize_arabic` 是独立公开函数、有 8 条独立测试；scan 内部只是组合它，职责已分离 |
| 未来可选项 | 若需再拆，正确位置是 glossary.py **内部**抽私有辅助步骤（如 `_prepare_entries` / `_prepare_paragraphs`），公共 API 与测试不变 |

### 三、经验记录

「单一职责」的判断单位是**函数承担的职责**（scan 的职责是产出命中报告），不是「函数内出现了几个操作」；归一化是完成该职责的准备步骤，属内聚而非职责混杂。另外，**分层职责**（UI / 业务逻辑）是更大尺度上的 SRP——把业务逻辑挪进 UI 层是反向违规。

## 2026-08-08 · 第 2 阶段需求确认与设计决策

### 一、需求确认（与用户逐项确认）

| 决策点 | 结论 |
|---|---|
| 翻译 API | 阿里云机器翻译 TranslateGeneral（RPC 风格、HMAC-SHA1 手写签名，**不引 SDK**），方向 ar→zh |
| 双模式 | `TRANSLATION_ENGINE` = mock（占位，默认）/ api（真实翻译）；非法值抛 ValueError |
| 缺 key 行为 | api 模式无 AK/Secret → **不报错**，回退占位译文 + 页面黄色提示 |
| 术语约束 | `build_translation_constraints` 正常生成（按段落过滤），但**不发送**——已核实阿里云该 API 无 context/术语参数；payload 预留 `params["Context"]` 注释位（阶段 3 接 LLM 时启用） |
| DeepSeek | key 仅写 .env 预留 + .env.example 占位；本阶段不读取不调用 |
| 逐段策略 | 逐段串行、一次一段；**一段失败中断整批**，异常携带段落号（1 基） |
| 依赖 | 允许新增 requests、python-dotenv；禁止 pandas/FastAPI/Dify/React/SQLite/LangChain |
| 环境变量 | TRANSLATION_* / ALIYUN_* 命名；dotenv 只在 app.py 顶部加载（modules/ 不 import，可测试性） |
| 测试 | 保留阶段 1 全部测试（占位文案逐字节不变）；新增约 25 条（配置/签名/payload/双模式/约束/错误路径） |
| 实施方式 | **Evaluator-Optimizer 双 Agent 协作**：Optimizer 实施 → Evaluator 只读审查（契约/测试/安全红线/风格）→ 循环至无 blocker |

### 二、开发中发现的问题记录

1. **空环境变量必须回落默认值**（冒烟发现）：`.env` 模板留空（`TRANSLATION_ENGINE=`）时读到空串，原实现按非法值抛 ValueError，导致「复制模板什么都不填」直接崩溃。已修：engine/timeout/endpoint/语言/场景统一「空串 = 未设置 = 默认值」；AK/SK 刻意不回落（空串正是「缺凭证 → 回退占位」的判定依据）。测试同步修正（空串不再视为非法）。
2. **api 模式空输入不发请求**：契约「空输入 → []」在 api 模式同样成立（先于配置加载与循环返回），补测试锁定。
3. **签名联调一次通过**：真实网络冒烟（`TRANSLATION_ENGINE=api` + .env 真 key）返回真实译文「欢迎来到阿拉伯项目」，验证签名算法、密钥、账号开通全部正确。测试内 known-answer 冻结值亦经独立 hmac 参考实现交叉验证。
4. **RPC 签名要点**（踩坑预防）：参数键按字典序排序后各自 percentEncode（safe="-_.~"，空格→%20）；StringToSign 的 `%2F` 会再被整体编码成 `%252F`（双重编码特征，勿手工再编码）；POST body 直接用规范化查询串保证「发送字节 == 签名字节」；HTTP 200 但 Code≠"200" 是业务错误（InvalidAccessKeyId / ServiceNotOpened / SignatureDoesNotMatch）。

### 三、验证结果

- 全量测试 73 条全绿（阶段 1 的 47 条 + 阶段 2 新增 26 条）
- 手动冒烟三状态全部通过：mock（占位，无警告）/ api 缺 key（占位 + fallback=True）/ api 真 key（真实译文）
- 安全核查：代码/测试/.env.example/日志无任何真实密钥；.env（gitignored）只存本地
- 用户密钥曾在聊天中暴露，联调通过后建议在阿里云/DeepSeek 控制台**轮换重置**

## 2026-08-08 · 阶段 2 收尾重构：配置拆分 settings.py

### 一、背景

用户反馈 translator.py（584 行）混杂「配置（常量/默认值）+ 类型定义（TranslationConfig、配置加载）+ 翻译逻辑」，文件繁琐、修改不便。提出新建 settings 文件集中管理。

### 二、决策（用户拍板）

| 决策点 | 结论 |
|---|---|
| 是否拆分 | **是**——新建 modules/settings.py 作为翻译配置中心 |
| 拆哪些 | 环境变量名（ENV_* 常量，改名只动一处）、引擎标识（MOCK/API_ENGINE）、默认值（DEFAULT_*）、TranslationConfig、load_translation_config |
| 异常体系 | **留在 translator.py**——错误是翻译行为的对外契约（app.py 用 except translator.TranslationError 捕获），与翻译逻辑强相关；配置模块不混杂错误类型 |
| 引用方式 | 引用方显式从正确模块导入（不做 re-export 转发），避免隐藏依赖：配置符号从 settings 取，翻译符号从 translator 取 |

### 三、经验记录

「文件过大、职责混杂」是重构信号。拆分依据是**内聚**：配置是「数据」、翻译是「行为」、异常是「行为的失败契约」——数据与行为分离是自然边界，失败契约跟随行为。纯搬迁重构后 73 条测试零改动全绿，验证「行为零变化」的重构正确性。

## 2026-08-09 · 第 3 阶段需求确认与设计决策

### 一、需求确认（与用户逐项确认）

| 决策点 | 结论 |
|---|---|
| 审校引擎 | DeepSeek / OpenAI 兼容接口（requests 直调，**不引 openai SDK**）；`REVIEW_ENGINE` = mock（占位报告，默认）/ api |
| 配置归属 | **全部进 settings.py**（统一配置中心：ReviewConfig + load_review_config，沿用 ENV_* 常量 / 空串回落 / 非法值抛中文 ValueError / api_key 刻意不回落） |
| 失败策略 | 与阶段 2 对称：api 缺 key → 回退占位报告 + 黄条（review_fallback）；调用失败（网络/业务/解析）→ 抛 `ReviewError` 家族 → 页面红条，**旧结果保留（api 翻译结果不丢）** |
| mock 报告 | 与阶段 1 占位文案**逐字节一致**（旧 3 条测试零改动） |
| URL 语义 | `DEEPSEEK_API_URL` 是 **base URL**（默认 https://api.deepseek.com，内部拼 /chat/completions 去重尾斜杠） |
| 系统定位 | **API 机器翻译是不可省略的主步骤，LLM 是不可缺少的校准辅助**——翻译 API 无法接入术语表（阿里云 TranslateGeneral 无 context 参数），术语一致性必须由 LLM 依据术语库把关；**LLM 报告以真实翻译结果为审校依据**，二者都执行 |
| 调试视图按钮 | st.radio 两态（默认全量视图）：仅 LLM 校准 = 只隐藏「双语对照」整节（术语/专名表仍显示，它们是审校依据）/ LLM 校准 + API 翻译结果 = 全量；**只影响展示，不影响 run_pipeline 执行** |
| 报告结构 | 8 项：文本概况 / 术语命中与风险 / 专名命中与统一性 / 重点风险句段 / 语体与领域适配 / 文化政治语境提醒 / 总体结论 / 人工复核建议 |
| 模板 | prompts/review_report_prompt.md 重写：「## 用户消息」为系统/用户消息分界；4 占位符在**用户段**各恰好 1 次（Evaluator 建议强化） |
| 测试 | 旧 3 条零改动 + 新增 26 条（monkeypatch 不打真实网络、假 key、不 import app.py） |
| 实施方式 | **Evaluator-Optimizer 双 Agent 协作**（本阶段延续）：Optimizer 实施 → Evaluator 只读审查 → 反馈循环至无 blocker |

### 二、开发中发现的问题记录（Evaluator 反馈）

1. **占位符校验应针对用户段**（审查发现）：原 `_load_prompt_template` 只校验 4 占位符在全模板各恰好 1 次——若占位符被误挪进系统消息段，校验通过但用户消息会残留字面 `{source_paragraphs}`。已修：标记与占位符校验全部针对 split 后的**用户段**，并校验「## 系统消息」「## 用户消息」两个标记都存在且顺序正确。
2. **模板头部说明不应发给 LLM**（审查发现）：文件标题行与维护者说明 blockquote 原会随系统消息发送（提示词噪音）。已修：系统段截到「## 系统消息」标记之后。
3. **计划计数笔误**（信息性）：.env.example 计划写「9 → 14」实际 9 → 13（DEEPSEEK_API_KEY 阶段 2 已存在，本次新增 4 个 + 1 个占位注释转正式），已同步修正计划文件。

### 三、验证结果

- 全量测试 99 条全绿（阶段 1/2 的 73 条零改动 + 阶段 3 新增 26 条）
- 冒烟：mock 占位（review_mode=mock）/ api 缺 key 回退（review_fallback=True）/ api 真 key 8 项报告 / api 假 key st.error「审校失败」旧结果保留；radio 两种视图切换正常
- 安全核查：代码/测试/文档无任何真实密钥；异常消息不含密钥；业务错误片段截断 200 字符；st.markdown 渲染 LLM 输出不传 unsafe_allow_html（防注入）
- 用户密钥曾在聊天中暴露，联调通过后建议在阿里云/DeepSeek 控制台**轮换重置**

## 2026-08-24 · 第 3.1 阶段确认：翻译结果两种展示选项

### 一、需求确认

用户提出当前「仅 LLM 校准（审校报告）/ LLM 校准 + API 翻译结果」两个选项不符合预期，应为：

1. **显示 LLM 纠正过后的翻译**
2. **显示原始 API 翻译 + LLM 纠正过后的翻译结果（两者都显示）**

关键澄清：原代码里 LLM 只生成 8 项审校报告（模板明确“不重翻全文”），并不存在“LLM 纠正后译文”。用户确认采用推荐方案：**增加 LLM 纠正译文生成 + 页面两个展示模式，保留审校报告作为辅助展示**。

### 二、设计决策

| 决策点 | 结论 |
|---|---|
| 生成方式 | `generate_review_bundle` 一次 LLM 调用同时返回 `report` 与 `corrected_translations`；`generate_review_report` 保留为只取 `report` 的兼容包装 |
| 提示词 | `prompts/review_report_prompt.md` 改为「先输出 `## 纠正后译文`，再输出 `## 审校报告`」；约束由“绝不重翻全文”改为“在给定译文基础上修正，不脱离原文另译” |
| 解析回退 | LLM 未按新版结构输出时：report 保留整段返回文本，corrected_translations 回退为原始 API 译文，避免页面崩溃 |
| mock/缺 key | 纠正译文使用「（占位纠正译文·第N段）待接入 LLM 纠正」，不把原始译文伪装成 LLM 纠正结果 |
| 页面视图 | radio 改为「显示 LLM 纠正过后的翻译 / 显示原始 API 翻译 + LLM 纠正过后的翻译结果（两者都显示）」，默认显示两者；审校报告始终保留在下方 |
| 并发与动态展示 | API 翻译多段采用 `ThreadPoolExecutor(4)` 并发请求；页面通过 `run_pipeline_progressive` + `st.empty` 动态显示「等待翻译 → 翻译完成 → LLM 审校中」；LLM 必须以整篇文章为输入，所以仍在全部段落翻译完成后才启动 |
| 测试 | 新增 4 条结果包相关单测 + 2 条并发翻译单测；原有 reviewer 测试兼容（旧函数仍走 bundle 但返回 report） |

### 三、验证情况

- `python -m py_compile` 通过；`run_pipeline` mock 冒烟返回 `corrected_translations` 且长度与段落一致
- 全量 `python -m pytest -q` 通过：**105 passed**（103 条原有 + 2 条并发翻译新增）

## 2026-08-24 · 第 3.2 阶段确认：LLM 直接翻译 / 修正 / 最终仲裁 + 四结果对比

### 一、需求确认

用户提出不能让 LLM 只“基于 API 译文修正”，而应执行完整多轮流程：

1. LLM 直接翻译原文，持有一个结果；
2. 基于 API 译文进行修正，持有一个结果；
3. 比对两个结果并继续结合原文，产出最终结果；
4. 最终仲裁以原文为最高依据，不能新加入原文未有的内容；
5. 页面最终把四个结果都显示出来。

### 二、设计决策

| 决策点 | 结论 |
|---|---|
| 生成方式 | `generate_review_bundle` **分三次独立调用**返回 `direct_translations` + `corrected_translations` + `final_translations` + `tradeoff_notes` + `report` |
| 提示词 | 三份独立模板：直接翻译 / 修正 / 最终仲裁；最终仲裁输出 `## 最终结果` / `## 翻译取舍说明` / `## 审校报告` |
| 仲裁原则 | **最终结果以原文为最高依据，禁止添加原文未有的信息** |
| 页面展示 | 移除原 radio，固定显示四结果对比 |
| 回退策略 | 某段结果解析失败时：direct/corrected 回退原始译文，final 优先回退 corrected |
| 测试 | 4 条 reviewer 结果包测试升级为四结果场景；全量 105 条通过 |

### 三、验证情况

- `python -m pytest -q`：**105 passed**
- Playwright 真实 API/LLM 模式验证：页面显示四结果对比，每段四种译文标签齐全，无控制台错误

## 2026-08-30 · 第 4 阶段确认：SQLite 持久化与历史记录

### 一、需求确认

团队不熟悉 SQLite，因此要求教学性、可读性、低复杂度。本阶段只做：

1. SQLite 保存翻译审校任务；
2. 查看历史任务；
3. 人工修改最终译文；
4. 保存老师/人工审校意见；
5. 页面重启后仍可读取历史。

### 二、设计决策

| 决策点 | 结论 |
|---|---|
| 存储方式 | 标准库 `sqlite3`，不引入 ORM |
| 数据库路径 | 默认 `data/translations.db`，可用 `DATABASE_PATH` 覆盖 |
| 表结构 | documents / paragraphs / translation_runs / paragraph_results / review_notes |
| 增量保存 | reviewer 拆成三个公开函数，流水线每步完成后立即保存 |
| 失败保留 | 任一阶段失败更新 partial/failed，已保存结果不丢 |
| 界面 | 不重写前端，在现有 Streamlit 底部增加历史记录、人工译文、审校意见 |
| 测试 | 新增 `tests/test_storage.py`，原 105 条全部保留 |

### 三、验证情况

- 全量 `python -m pytest -q`：**118 passed**
