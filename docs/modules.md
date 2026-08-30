# 模块功能说明（供审查）

> 本文档说明第 2 阶段各模块的作用、函数契约与相互调用关系。
> 修改代码后请同步更新本文档。

## 总览：数据流

```
用户粘贴阿语文本
      │
      ▼
app.py ──run_pipeline(text)────▶  modules/segmenter.py   → 段落列表
      │（五个模块并列调用，互不调用）│
      │                       ├──▶ modules/glossary.py（加载 terms.csv 六列）
      │                       │         └──▶ scan_glossary → 术语命中
      │                       ├──▶ modules/glossary.py（加载 proper_names.csv 四列）
      │                       │         └──▶ scan_glossary → 专名命中
      │                       ├──▶ modules/translator.py → 译文（mock 占位 / api 阿里云）
      │                       └──▶ modules/reviewer.py → 审校报告（mock 占位 / api LLM）
      │                                 └──▶ prompts/review_report_prompt.md（提示词模板）
      │
      ▼
  五个展示区：双语对照 / 术语命中 / 专名命中 / 译文 / 报告
```

设计原则：**纯函数原则** —— modules/ 下所有函数不涉及 UI、不依赖全局状态，
数据文件路径作为参数传入；错误（如文件缺失）由函数抛出，UI 层统一转成提示。
各模块是**并列关系**：全部由 app.py 的 run_pipeline 按顺序调用，模块之间互不调用。

---

## modules/segmenter.py — 段落切分

**作用**：把用户粘贴的整篇文本切成段落列表。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `segment_paragraphs(text: str)` | 整篇文本（可含 \r\n、空行） | `list[str]` 段落列表 | 有空行按空行分段；无空行按换行分段（每行一段）；空输入返回 [] |

**调用方**：app.py 的 `run_pipeline`。段落编号不在本模块做（列表索引即编号）。

---

## modules/glossary.py — 术语库/专名库（核心）

**作用**：阿语文本归一化、加载两个 CSV 词库、在段落中扫描词条命中。两库共用同一套函数。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `normalize_arabic(text: str)` | 任意文本 | `str` 归一化结果 | 仅四项：去变音符、去 tatweel、去 bidi 控制符、أإآٱ→ا；不做 ى→ي、ة→ه、ؤ/ئ/ء |
| `load_glossary(path)` | CSV 路径（str 或 Path） | `list[dict]` | dict 键 = CSV 表头名；「阿语原文」空行跳过；缺文件抛 FileNotFoundError |
| `scan_glossary(paragraphs, entries)` | 段落列表 + 词条列表 | `list[dict]` 命中列表 | 归一化后纯子串匹配；**按词条聚合**：命中 = 词条原字段 + paragraphs（出现段落号列表）+ count（全文累计次数）；按首次出现段落排序。该函数对entry已经进行过一次归一化了 |

**命中 dict 字段**（键 = 表头名，两库不同；外加两个统计字段）：
- 术语命中：阿语原文 / 中文译文 / 类别 / 领域 / 备注 / 处理方式 + paragraphs + count
- 专名命中：阿语原文 / 中文译文 / 类别 / 备注 + paragraphs + count

**聚合规则**：同一词条跨多段出现只产出一条命中（段落号并入 paragraphs 列表、次数累计进 count），表格不会出现重复词条行。

**已知取舍**：纯子串匹配不做词边界判断，会命中派生词（如词条 فلسطين 命中正文 الفلسطينية），属阶段 1 有意为之，后续阶段再加边界规则。

**调用方**：app.py 的 `run_pipeline`；normalize_arabic 被 scan_glossary 内部调用。

---

## modules/settings.py — 翻译与审校配置中心（阶段 2 重构新增，阶段 3 扩展）

**作用**：集中管理翻译与 LLM 审校的全部配置——环境变量名（`ENV_*` 常量，改名只动一处）、
引擎标识（`MOCK_ENGINE` / `API_ENGINE` 两引擎共用）、默认值（`DEFAULT_*`）、配置对象
（`TranslationConfig` / `ReviewConfig`，均 frozen dataclass + `has_credentials`）与配置加载
（`load_translation_config()` / `load_review_config()`，唯一读取 os.environ 的地方）。

| 函数/对象 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `TranslationConfig` | 构造参数 | 配置对象 | frozen 不可变；`has_credentials` = AK 与 Secret 都非空 |
| `load_translation_config()` | 无（读 os.environ） | `TranslationConfig` | 非法 engine / 非正整数 timeout 抛中文 ValueError；空串 = 未设置 = 默认值 |
| `ReviewConfig` | 构造参数 | 配置对象 | engine / api_key / api_url / model / timeout_seconds；`has_credentials` = api_key 非空 |
| `load_review_config()` | 无（读 os.environ） | `ReviewConfig` | 与翻译版完全对称；**api_key 刻意不回落**（空串是「缺凭证 → 回退占位」的判定依据）；`DEEPSEEK_API_URL` 是 base URL（默认 https://api.deepseek.com，无尾斜杠） |

**职责约定**：本模块是翻译与审校配置唯一读取环境变量的地方；translator.py / reviewer.py 只通过
参数接收配置；异常体系刻意留在各自业务模块（translator.py 的 TranslationError 家族、
reviewer.py 的 ReviewError 家族——错误是业务行为的对外契约）；dotenv 仍只在 app.py 加载。

**调用方**：translator.py、reviewer.py（导入常量与配置）、app.py（取模式与回退判定）。

---

## modules/translator.py — 翻译（阶段 2 双模式；3.1 增加并发入口）

**作用**：为每个段落生成译文。双模式由环境变量 `TRANSLATION_ENGINE` 决定：
`mock`（占位译文，不联网，默认）/ `api`（调用阿里云机器翻译 TranslateGeneral）。
`translate_paragraphs` 是串行兼容入口；`translate_paragraphs_parallel` 是并发入口，
用 `ThreadPoolExecutor` 默认 4 线程同时请求多段，并支持每段完成回调，供 Streamlit
动态展示“等待翻译/翻译完成”。
api 模式缺密钥时自动回退占位（不抛错）；任一段翻译失败即中断整批，异常携带段落号。
配置的定义与加载集中在 modules/settings.py（见上节），本模块只按需导入。

**公开函数**：

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `translate_paragraphs(paragraphs, term_hits=None, name_hits=None)` | 阿语段落列表 + 可选两类命中 | `list[str]` 译文列表 | 串行兼容入口；与输入同长度同顺序；空输入返回 []（api 模式也不发请求）；mock 或无凭证 → 占位译文（文案与阶段 1 逐字节一致） |
| `translate_paragraphs_parallel(paragraphs, term_hits=None, name_hits=None, *, on_translation=None, max_workers=4)` | 阿语段落列表 + 可选命中 + 可选回调 | `list[str]` 译文列表 | 并发入口；api 模式多段同时请求；`on_translation(index, translation)` 在主线程回调（0 基 index），可安全更新 Streamlit 占位符；任一段失败取消未开始请求并抛异常 |
| `load_translation_config()` | 无（读 os.environ） | `TranslationConfig` | 模块内唯一读环境变量的函数；非法 engine / 非正整数 timeout 抛中文 ValueError |
| `build_translation_constraints(term_hits, name_hits, paragraph_no=None)` | 两类命中 + 可选段号 | `str` 约束文本 | 按「该段是否命中」过滤（None 取全部）；格式「【术语约束】…【专名约束】…」；无命中返回 ""。**只生成不发送**（阿里云该 API 无 context 参数，供阶段 3 接 LLM 用） |

**异常体系**（定义在本模块，消息全中文、不含密钥）：
- `TranslationError(Exception)`：基类，带 `paragraph_no`（1 基，批外为 None）
- `TranslationNetworkError`：连接失败 / 超时 / HTTP 非 200
- `TranslationBusinessError`：HTTP 200 但 Code≠"200"（如 InvalidAccessKeyId / ServiceNotOpened），附阿里云错误码
- `TranslationParseError`：响应非 JSON / 缺 Data.Translated

**内部结构**（RPC 签名链，纯函数可单测）：
`_percent_encode`（urllib quote, safe="-_.~"）→ `_canonicalized_query_string`（键字典序）→
`_build_string_to_sign`（`POST&%2F&` + 整体再编码，`%252F` 双重编码特征）→
`_compute_signature`（base64(hmac-sha1(secret+"&", …))）；`_build_request_payload` 构造公共参数
（Action=TranslateGeneral、Version=2018-10-12、Format=JSON、SignatureNonce=uuid4、Timestamp=ISO8601 UTC）
与业务参数（FormatType=text、Scene、Source/TargetLanguage、SourceText），预留 `# params["Context"] = constraints`
注释位（未来接 LLM 时启用）；POST body 直接用规范化查询串（发送字节 == 签名字节），timeout 可配。

**调用方**：app.py 的 `run_pipeline`（传入 term_hits/name_hits 构建约束）。环境变量与默认值见 modules/settings.py 节。

---

## modules/reviewer.py — 四结果 + 审校报告（阶段 3 / 3.1 / 3.2 多结果）

**作用**：生成「直接翻译 + 修正结果 + 最终仲裁 + 审校报告」结果包。双模式由环境变量 `REVIEW_ENGINE` 决定：
`mock`（占位, 不联网，默认，报告文案与阶段 1 逐字节一致）/ `api`（调用 DeepSeek / OpenAI 兼容接口，
**分三次独立调用**，每次使用独立提示词模板）：
①直接翻译原文（不提供 API 译文）；②基于原文 + API 译文修正；③结合原文仲裁最终结果 + 翻译取舍说明 + 8 项报告（不提供 API 译文）。
api 模式缺密钥时自动回退占位（不抛错）；调用失败（网络/业务/解析）抛 `ReviewError` 家族。
最终仲裁以原文为最高依据，禁止添加原文未有的信息。

**公开函数**：

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `generate_review_bundle(paragraphs, translations, term_hits, name_hits)` | 段落、原始 API 译文、两类命中列表 | `dict`：`{"report": str, "direct_translations": list[str], "corrected_translations": list[str], "final_translations": list[str], "tradeoff_notes": str}` | 三次独立调用，各阶段只带必要输入。空输入短路 → 占位（api 也不发请求）；mock 或缺 key → 占位；否则依次调用直接翻译 / 修正 / 最终仲裁 |
| `generate_review_report(...)` | 同上 | `str` 报告文本 | 兼容旧调用的薄包装：调用 `generate_review_bundle` 后只返回 `"report"` |

**异常体系**（定义在本模块，消息全中文、不含密钥）：
- `ReviewError(Exception)`：基类
- `ReviewNetworkError`：超时 / 连接失败
- `ReviewBusinessError`：HTTP 非 200，附 status_code 与响应片段（text[:200] 截断）
- `ReviewParseError`：响应非 JSON / 缺 choices / 缺 message.content

**私有函数**（纯函数，可单测）：
- `_generate_mock_bundle(...)`：mock/缺 key 时返回占位报告 + 占位直接翻译/修正/最终结果 + 占位取舍说明
- `_parse_review_bundle(content, paragraphs, corrected_translations)`：解析“最终仲裁”回复，按 `## 最终结果` / `## 翻译取舍说明` / `## 审校报告` 三个二级标题拆出最终译文、取舍说明与报告；未按结构输出时安全回退
- `_parse_numbered_translations(text, paragraphs, fallback)`：从「第N段：…」文本解析某轮译文；提取条数与段落数不符时回退传入的兜底列表
- `_load_prompt_template(path=None, placeholders=None)`：读指定模板（UTF-8）；缺文件抛 FileNotFoundError；结构校验——「## 系统消息」「## 用户消息」两个标记都存在且顺序正确，且 placeholders 指定的占位符在**用户段**各恰好 1 次
- `_format_hits_text(term_hits, name_hits)`：两库统一格式化（.get 兼容键差异，专名无「领域」「处理方式」）：`【术语命中】共 N 条\n- 阿语原文「…」→ 中文译文「…」（类别：…；领域：…；备注：…；处理方式：…；出现段落：1, 4, 5；出现次数：7）`；无命中输出「无」；处理方式保留 CSV 原值（语义说明在模板）
- `_format_numbered(items, label)`：段落/译文编号化「第N段：…」，LLM 可引用段号
- `_render_user_message(template, replacements)`：按「## 用户消息」split，只对用户段做 `replacements` 中占位符的 `str.replace`；系统段截到「## 系统消息」标记之后
- `_call_llm(config, prompt_path, placeholders, replacements)`：执行一次独立 LLM 调用（读模板 → 拆段 → 替换 → POST → 解析），POST 带 `Authorization: Bearer <key>` 与 `Content-Type: application/json`，timeout 来自配置
- `_build_api_url(config)`：`config.api_url.rstrip("/") + "/chat/completions"`（去重尾斜杠）
- `_build_request_payload(config, system, user)`：`{"model", "messages": [{role: system}, {role: user}], "temperature": 0.3}`
- `_parse_review_response(response)`：超时/连接失败 → Network；HTTP 非 200 → Business（状态码 + text[:200]）；非 JSON / 缺 choices / 缺 message.content → Parse；成功返回 content

**模板文件**（prompts/ 目录下三份）：
- `direct_translation_prompt.md`：直接翻译阶段，占位符 source/term/name，不包含 API 译文
- `correct_translation_prompt.md`：修正阶段，占位符 source/translations/term/name
- `review_report_prompt.md`：最终仲裁阶段，输出 `## 最终结果` / `## 翻译取舍说明` / `## 审校报告`，占位符 source/direct/corrected/term/name，不包含 API 译文

每个模板都以「## 用户消息」为系统/用户消息分界；系统消息含角色设定、输出结构、约束。

**调用方**：app.py 的 `run_pipeline`。环境变量与默认值见 modules/settings.py 节。

---

## modules/storage.py — 审校记录持久化（阶段 1 占位）

**作用**：保存每次审校记录，供查询追溯。阶段 1 **不实现任何功能、不参与流水线**，仅保留文件与设计说明（SQLite 表结构示意：reviews(id, created_at, source_text, translations, term_hits, report)；未来函数 save_review / list_reviews）。

**未来演化（阶段 4）**：实现 SQLite 连接、建表与上述两个函数，接入流水线。

**调用方**：阶段 1 无人调用。

---

## pipeline.py — 核心流水线（与 UI 解耦）

**作用**：从 app.py 拆分出的流水线编排，包含 `run_pipeline` 与 `run_pipeline_progressive`；
负责切分、词库扫描、并发翻译、LLM 多轮结果包组装；不包含任何 `st.*` UI 代码。

---

## streamlit_ui.py — Streamlit 结果渲染

**作用**：从 app.py 拆分出的展示层，包含 `render_results`、`_hits_to_rows`、`load_sample`；
负责四结果对比、翻译取舍说明、术语/专名命中表、审校报告渲染。

---

## app.py — Streamlit 页面（唯一 UI 入口）

**作用**：页面布局与交互；调用各模块组成流水线；展示结果。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `run_pipeline(text: str)` | 整篇阿语文本 | `dict`（13 键：paragraphs/translations/direct_translations/corrected_translations/final_translations/tradeoff_notes/term_hits/name_hits/report/translation_mode/translation_fallback/review_mode/review_fallback） | 完整入口（无 UI 回调），内部委托 `run_pipeline_progressive`；三个 LLM 列表分别为直接翻译/修正结果/最终结果；tradeoff_notes 为翻译取舍说明；translation_mode/review_mode 等为模式与回退标志 |
| `run_pipeline_progressive(text, *, on_paragraphs=None, on_translation=None, on_review_start=None, on_review_done=None)` | 整篇阿语文本 + 可选回调 | `dict`（同上） | 渐进式流水线：切分完成回调、每段翻译完成回调、LLM 开始/完成回调；页面用它实现“等待翻译→翻译完成→LLM 审校中”的动态展示 |
| `_load_sample()` | 无 | `str` | 读取 data/samples/politics_001.txt 预填输入框 |
| `_hits_to_rows(hits, columns)` | 命中列表 + 要展示的列 | `list[dict]` 展示行 | 统一列序，供 st.dataframe 渲染 |
| `render_results(results)` | run_pipeline 的结果 dict | 无（直接渲染页面） | 渲染「四结果对比/术语命中/专名命中/审校报告」；四结果固定展示：原始 API 译文、LLM 直接翻译、LLM 修正结果、LLM 最终结果 |

**页面布局**：标题 → 阿语输入框（预填样例）→ 「开始翻译与审校」按钮 → 四结果对比（阿语 RTL 右对齐；固定展示原始 API 译文 / LLM 直接翻译 / LLM 修正结果 / LLM 最终结果）→ 翻译取舍说明 → 术语命中表（六列）→ 专名命中表（四列）→ 审校报告区。

**关键实现**：
- 顶部 `load_dotenv(BASE_DIR / ".env")` 加载本地密钥（全项目唯一 import dotenv 处）
- 结果存 `st.session_state["results"]`，防止按钮后重跑丢失；异常时不覆盖 → 旧结果保留
- 按钮点击后使用 `run_pipeline_progressive`：先按段落创建 `st.empty` 占位符，**原文立即显示**，译文位置显示“等待翻译…”；某段并发翻译完成后立刻在该段原文下方补充译文；全部翻译完成后显示“正在请求 LLM 多轮处理…”，LLM 返回后清空临时占位符并渲染完整结果区
- RTL 用独立 CSS class `.ar-para`，只影响阿语区域
- 用户文本先 `html.escape` 再拼 HTML，防注入
- 处理方式列做显示映射：force_check→强检查、suggest→推荐检查、context_warning→语境检查
- 异常捕获（按业务发生顺序）：FileNotFoundError（数据缺失）→ st.error；translator.TranslationError（翻译失败，消息含段号）→ st.error；reviewer.ReviewError（审校失败）→ st.error；ValueError（配置错误）→ st.error
- fallback 为真时结果区顶部 st.warning「已配置 API 模式但未配置密钥…」/「已配置 LLM 校准 API 模式但未配置密钥…」；译文标签随模式变化（api 真实译文不带「占位」字样）
- 报告区：api 真报告 → st.markdown（LLM 输出是 markdown，**不传 unsafe_allow_html** 防注入）+ caption 提示供人工复核；mock/回退 → st.info 占位样式

**调用方**：无（页面入口，运行 `streamlit run app.py`）。

---

## tests/ — 单元测试

| 文件 | 覆盖范围 |
|---|---|
| test_segmenter.py | 切分规则全边界（空/纯空白/无空行/空行分组/Windows 换行/段内换行） |
| test_glossary.py | 归一化四类字符、防过度归一化；加载（六列/四列/BOM/缺文件/空行/引号）；扫描（变体拼写/段号/次数/多词/纯子串/空输入） |
| test_translator.py | 阶段 1 保留 3 条（长度一致、含标记、空输入）+ 阶段 2 新增 26 条：配置 4、签名 4、payload 3、双模式 6、约束 3、错误路径 6；阶段 3.1 新增 2 条：并发入口全段落翻译+回调、并发入口 mock 不联网 |
| test_reviewer.py | 阶段 1 保留 3 条（统计数字、占位标记、返回类型）+ 阶段 3 新增 26 条（配置/双模式/模板/hits/请求/错误路径）+ 阶段 3.1/3.2 新增 4 条：四结果 mock 占位、api 解析四结果、无结构标记回退、最终结果段数不符回退 |
| test_data_integrity.py | 数据文件可加载且条数达标、样例文本切分段数（可选） |

运行：项目根目录 `python -m pytest -v`。
