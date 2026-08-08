# 阶段一完成报告（v1.0）

| 项目 | 内容 |
|---|---|
| 报告日期 | 2026-08-07 |
| 版本 | v1.0（提交信息：v1.0 阶段一完成） |
| 状态 | 已完成，全部测试与端到端验证通过 |
| 代码位置 | 分支 `feature/stage-1`，待管理员合并（PR：https://github.com/kadam-kdvcs/Tarjuman/pull/new/feature/stage-1） |

---

## 1. 项目背景

面向**阿语政治外交 / 国际传播文本**的翻译审校辅助 MVP。用户粘贴一段阿语文本，系统切分段落、扫描术语库与专名库、展示双语对照与命中结果，为后续接入翻译 API 与 LLM 审校搭建框架。

技术路线（已确认）：Python + Streamlit + 标准库 csv，分阶段开发，本阶段**不接任何外部服务**（无翻译 API、无 LLM、无 SQLite、无登录权限）。

## 2. 阶段一目标

1. 搭建可运行的 Streamlit 页面（标题 / 输入框 / 按钮 / 五个展示区）
2. 跑通纯本地流水线：切分 → 加载词库 → 扫描命中 → 占位译文 → 占位报告
3. 建立术语库 / 专名库 CSV 数据格式与示例数据
4. 全部函数模块化、可测试、文档齐全（含 CLAUDE.md 与模块说明）
5. 通过单元测试与浏览器端到端验证

## 3. 交付功能

- 阿语文本输入（预填示例文本，支持粘贴多段）
- 段落切分：**有空行按空行分段；无空行按换行分段（每行一段）**
- 术语库扫描（terms.csv 六列）与专名库扫描（proper_names.csv 四列）
- 阿语归一化后纯子串匹配，命中结果**按词条聚合**展示
- 阿中双语对照区（阿语 RTL 右对齐显示）
- 术语命中表（8 列：阿语原文 / 中文译文 / 类别 / 领域 / 备注 / 处理方式 / 段落 / 出现次数）
- 专名命中表（6 列：阿语原文 / 中文译文 / 类别 / 备注 / 段落 / 出现次数）
- 占位译文与占位审校报告（阶段 2/3 替换为真实 API 与 LLM）
- 处理方式列中文映射：force_check→强检查、suggest→推荐检查、context_warning→语境检查

## 4. 技术实现

### 4.1 架构与数据流

```
用户粘贴阿语文本
      │
      ▼
app.py ──run_pipeline(text)──▶  modules/segmenter.py   → 段落列表
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

### 4.2 文件清单（21 个文件，全部新建）

| 文件 | 说明 |
|---|---|
| app.py | Streamlit 页面（唯一 UI 入口）；run_pipeline 为顶层纯流水线函数 |
| modules/segmenter.py | 段落切分（纯函数） |
| modules/glossary.py | 归一化 + CSV 加载 + 命中扫描（核心） |
| modules/translator.py | 占位译文（阶段 2 接 API，签名不变） |
| modules/reviewer.py | 占位报告（阶段 3 接 LLM，签名不变） |
| modules/storage.py | 占位（阶段 4 接 SQLite） |
| data/terms.csv | 术语库示例 10 条（六列） |
| data/proper_names.csv | 专名库示例 5 条（四列） |
| data/samples/politics_001.txt | 示例阿语短文 5 段（含变音符） |
| tests/ | 5 个测试文件，47 个用例 |
| README.md / CLAUDE.md | 项目说明 / 给后续 Claude 会话的说明 |
| docs/meeting_notes.md / docs/modules.md | 决策日志 / 模块说明 |
| prompts/review_report_prompt.md | LLM 审校提示词模板（阶段 3 启用） |

### 4.3 关键设计决策

| 决策 | 说明 |
|---|---|
| 纯函数原则 | modules/ 下函数无 UI、无全局状态、路径作参数；错误抛出由 UI 层转提示；可独立测试 |
| CSV 键名 = 表头名 | 两库表头不同（六列 vs 四列），dict 键直接用表头中文名，一套加载/扫描函数共用 |
| 编码约定 | 全部 UTF-8；CSV 用 utf-8-sig（Excel 兼容）；csv 模块必须 newline="" |
| 阿语归一化（仅四项） | 去变音符、去 tatweel、去 bidi 控制符、أإآٱ→ا；**不做** ى→ي、ة→ه（防误配）、ؤ/ئ/ء（词义一部分） |
| 匹配方式 | 归一化后纯子串匹配，无词边界（命中派生词为已知取舍） |
| 命中聚合 | 按词条聚合：一行一词条，出现次数全文累计，段落列列出全部出现段落（用户提出，原按段落分组有重复行） |
| RTL 显示 | CSS 独立 class `.ar-para`（direction: rtl）只影响阿语区域；用户文本先 html.escape 防注入 |
| session_state | 仅 1 个键 "results" 存结果，防止重跑丢失 |
| 占位策略 | translator / reviewer 返回占位内容回填统计数字，验证链路贯通，签名预留 |

## 5. 测试与验证

### 5.1 单元测试（47 个全部通过）

| 测试文件 | 覆盖 |
|---|---|
| test_segmenter.py（10 个） | 空文本 / 纯空白 / 单行 / 无空行按行分段 / 空行分组 / Windows 换行 / 空行含空格 / 尾随空行 / 段内换行保留 / 首尾空白 |
| test_glossary.py（27 个） | 归一化 8 项（含防过度归一化回归）+ 加载 7 项（六列/四列/BOM/缺文件/空行/引号/空文件）+ 扫描 12 项（字段/变体拼写/段号/同段多次/多词/纯子串/聚合/混合次数/排序/空输入） |
| test_translator.py（3 个） | 长度一致 / 含占位标记 / 空输入 |
| test_reviewer.py（3 个） | 条数与次数统计 / 占位标记 / 返回类型 |
| test_data_integrity.py（4 个） | 数据文件可加载、条数达标、表头完整、样例文本含变音符 |

运行：项目根目录 `python -m pytest -v`

### 5.2 命令行验证（python -c 调用 run_pipeline）

示例文本处理结果：5 段 → 术语命中 **8 条（共 14 次）**，专名命中 **5 条（共 6 次）**，报告：`本次共 5 段，术语命中 8 条（共 14 次），专名命中 5 条（共 6 次）`

### 5.3 浏览器端到端验证（Playwright 实测）

- 页面元素：标题、说明、预填示例文本的输入框、「开始翻译与审校」按钮 ✅
- 点击按钮：双语对照 5 段（阿语 RTL 右对齐，`direction: rtl; text-align: right` 实测生效）✅
- 术语表 8 行无重复（如 السلام → 段落 [1,4,5] 共 3 次）✅
- 专名表 5 行无重复（如 فلسطين → 段落 [4,5] 共 2 次，含派生词命中预期行为）✅
- 审校报告统计正确 ✅
- 修改输入框不点按钮 → 结果区保持（session_state 生效）✅

## 6. 开发过程中发现并解决的问题

1. **段落首尾空白**：按空行分组时段落未 strip，导致测试失败 → 已修复 + 测试覆盖
2. **示例文本「للدولة」拼写核查**：一度误加定冠词 alif，经阿语拼写规则核实（介词 ل + 定冠词 ال 合并时 alif 省略）后还原。由此发现固有边界：词条「الدولة」不会命中「للدولة」（详见已知取舍）
3. **命中聚合粒度**（用户反馈）：原按（词条 × 段落）分组导致跨段重复行 → 改为按词条聚合，段落列列出全部段落、次数全文累计，报告同步改为「去重条数 + 累计次数」
4. **Streamlit 模块缓存**：修改 modules/ 下代码后服务不生效（Python sys.modules 缓存），需重启服务 —— 已记入决策日志
5. **本机 Git 配置问题**：全局证书路径失效（Git 安装目录被移动）→ 仓库级设置 `http.sslCAInfo` 指向 Python certifi 证书包（仅影响本项目）；推送权限 403 → 管理员添加协作者后解决

## 7. 已知取舍与限制（阶段 1 有意为之）

1. **纯子串匹配无词边界**：词条「فلسطين」会命中派生词「الفلسطينية」（预期行为，后续阶段加边界规则）
2. **不做 ى→ي、ة→ه 归一化**：宁可漏配不误配（如 سنة/سنه）
3. **定冠词 alif 省略类差异不命中**：「للدولة」不命中「الدولة」（拼写规则导致，后续需词形规则）
4. **不返回字符位置**：命中不含 start/end 偏移（后续需要高亮时再加）
5. **占位译文 / 占位报告**：无真实翻译与 LLM 审校
6. **无持久化**：审校记录不保存（阶段 4 接 SQLite）

## 8. 版本控制状态

```
feature/stage-1 分支（已推送，待管理员合并）
* 66df0e6 merge: 合并 GitHub 初始化文件（保留项目 README/.gitignore，纳入 LICENSE）
* f3ea615 v1.0 阶段一完成        ← 阶段 1 全部代码（21 个文件）
* fb74608 Initial commit          ← 远端原有提交
```

PR 入口：https://github.com/kadam-kdvcs/Tarjuman/pull/new/feature/stage-1

## 9. 后续路线图

| 阶段 | 内容 | 接口变化 |
|---|---|---|
| 阶段 2 | 接入真实翻译 API | translator.py 内部替换；新增 .env.example（Key 走环境变量） |
| 阶段 3 | LLM 生成审校报告 | reviewer.py 内部替换；使用 prompts/review_report_prompt.md 模板 |
| 阶段 4 | SQLite 保存审校记录 + 老师审校意见 | storage.py 实现 save_review / list_reviews |
| 后续可选 | 词边界规则、命中位置高亮、语料库支持 | glossary.py 扩展 |

## 10. 快速验证方式

```bash
# 运行测试（项目根目录）
python -m pytest -v

# 启动页面（项目根目录）
streamlit run app.py
```

注意：修改 modules/ 下代码后需重启 Streamlit 服务（模块缓存问题，见第 6 节）。
