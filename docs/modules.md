# 模块功能说明（供审查）

> 本文档说明第 1 阶段各模块的作用、函数契约与相互调用关系。
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
      │                       ├──▶ modules/translator.py → 占位译文
      │                       └──▶ modules/reviewer.py → 占位审校报告
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
| `scan_glossary(paragraphs, entries)` | 段落列表 + 词条列表 | `list[dict]` 命中列表 | 归一化后纯子串匹配；**按词条聚合**：命中 = 词条原字段 + paragraphs（出现段落号列表）+ count（全文累计次数）；按首次出现段落排序 |

**命中 dict 字段**（键 = 表头名，两库不同；外加两个统计字段）：
- 术语命中：阿语原文 / 中文译文 / 类别 / 领域 / 备注 / 处理方式 + paragraphs + count
- 专名命中：阿语原文 / 中文译文 / 类别 / 备注 + paragraphs + count

**聚合规则**：同一词条跨多段出现只产出一条命中（段落号并入 paragraphs 列表、次数累计进 count），表格不会出现重复词条行。

**已知取舍**：纯子串匹配不做词边界判断，会命中派生词（如词条 فلسطين 命中正文 الفلسطينية），属阶段 1 有意为之，后续阶段再加边界规则。

**调用方**：app.py 的 `run_pipeline`；normalize_arabic 被 scan_glossary 内部调用。

---

## modules/translator.py — 翻译（阶段 1 占位）

**作用**：为每个段落生成译文。阶段 1 不调用任何 API，只生成占位译文验证数据流。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `translate_paragraphs(paragraphs: list[str])` | 阿语段落列表 | `list[str]` 译文列表 | 与输入同长度同顺序；空输入返回 [] |

**未来演化（阶段 2）**：保持签名不变，内部替换为真实翻译 API 调用。

**调用方**：app.py 的 `run_pipeline`。

---

## modules/reviewer.py — 审校报告（阶段 1 占位）

**作用**：生成审校报告。阶段 1 不调用 LLM，只回填统计数字验证链路贯通。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `generate_review_report(paragraphs, translations, term_hits, name_hits)` | 段落、译文、两类命中列表 | `str` 报告文本（markdown） | 占位报告含段落数与命中计数 |

**未来演化（阶段 3）**：保持签名不变，内部替换为读取 prompts/review_report_prompt.md 模板并调用 LLM。

**调用方**：app.py 的 `run_pipeline`。

---

## modules/storage.py — 审校记录持久化（阶段 1 占位）

**作用**：保存每次审校记录，供查询追溯。阶段 1 **不实现任何功能、不参与流水线**，仅保留文件与设计说明（SQLite 表结构示意：reviews(id, created_at, source_text, translations, term_hits, report)；未来函数 save_review / list_reviews）。

**未来演化（阶段 4）**：实现 SQLite 连接、建表与上述两个函数，接入流水线。

**调用方**：阶段 1 无人调用。

---

## app.py — Streamlit 页面（唯一 UI 入口）

**作用**：页面布局与交互；调用各模块组成流水线；展示结果。

| 函数 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `run_pipeline(text: str)` | 整篇阿语文本 | `dict`（paragraphs/translations/term_hits/name_hits/report） | 纯本地流水线，不依赖 UI，可在命令行直接调用 |
| `_load_sample()` | 无 | `str` | 读取 data/samples/politics_001.txt 预填输入框 |
| `_hits_to_rows(hits, columns)` | 命中列表 + 要展示的列 | `list[dict]` 展示行 | 统一列序，供 st.dataframe 渲染 |
| `render_results(results)` | run_pipeline 的结果 dict | 无（直接渲染页面） | 渲染五个展示区 |

**页面布局**：标题 → 阿语输入框（预填样例）→ 「开始翻译与审校」按钮 → 双语对照（阿语 RTL 右对齐）→ 术语命中表（六列）→ 专名命中表（四列）→ 审校报告区。

**关键实现**：
- 结果存 `st.session_state["results"]`，防止按钮后重跑丢失
- RTL 用独立 CSS class `.ar-para`，只影响阿语区域
- 用户文本先 `html.escape` 再拼 HTML，防注入
- 处理方式列做显示映射：force_check→强检查、suggest→推荐检查、context_warning→语境检查
- 数据文件缺失时捕获 FileNotFoundError 转 st.error 提示

**调用方**：无（页面入口，运行 `streamlit run app.py`）。

---

## tests/ — 单元测试

| 文件 | 覆盖范围 |
|---|---|
| test_segmenter.py | 切分规则全边界（空/纯空白/无空行/空行分组/Windows 换行/段内换行） |
| test_glossary.py | 归一化四类字符、防过度归一化；加载（六列/四列/BOM/缺文件/空行/引号）；扫描（变体拼写/段号/次数/多词/纯子串/空输入） |
| test_translator.py | 占位译文长度一致、含标记、空输入 |
| test_reviewer.py | 报告含统计数字、含占位标记、返回类型 |
| test_data_integrity.py | 数据文件可加载且条数达标、样例文本切分段数（可选） |

运行：项目根目录 `python -m pytest -v`。
