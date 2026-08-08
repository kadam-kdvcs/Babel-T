# CLAUDE.md — 给后续 Claude 会话的项目说明

## 项目一句话定位

面向阿语政治外交 / 国际传播文本的翻译审校辅助 MVP：用户粘贴阿语文本 → 切分段落 → 扫描术语库/专名库 → 展示双语对照与命中 → 生成占位译文与审校报告（后续阶段接翻译 API 与 LLM）。

## 技术栈与约束（不可擅自更改）

- Python 3.13 + Streamlit + 标准库 csv（**不 import pandas**）+ requests + python-dotenv
- 阶段 2 已接翻译 API：阿里云机器翻译（TranslateGeneral，RPC 手写 HMAC-SHA1 签名，双模式 mock/api）；暂不接 LLM / SQLite（第 3/4 阶段引入）
- 不用 React/Vue/FastAPI/Django/Flask/Dify；不做登录权限、部署、PDF/Word
- API Key 绝不写入代码；需要时用 .env / 环境变量 + .env.example
- 所有路径相对路径；所有 open() 显式 encoding="utf-8" / "utf-8-sig"；CSV 必须 newline=""
- 运行环境 Windows（默认 GBK 编码，读写文件务必显式指定 UTF-8）

## 常用命令（必须在项目根目录运行）

```bash
streamlit run app.py        # 启动页面
python -m pytest -v         # 运行全部测试
```

modules/ 不是包（无 __init__.py），依赖「从根目录运行」把 cwd 加入 sys.path。

## 目录结构

```
app.py                      # 唯一 UI 入口（Streamlit）
.env.example                # 环境变量模板（只含占位说明，Key 留空）
.env                        # 本地真实密钥（gitignored，绝不提交）
modules/                    # 纯函数模块：segmenter / glossary / settings / translator / reviewer / storage
data/                       # terms.csv（六列）、proper_names.csv（四列）、samples/
tests/                      # pytest 测试
prompts/review_report_prompt.md   # LLM 审校提示词模板（阶段 3 启用）
docs/                       # meeting_notes.md 决策记录、modules.md 模块说明
```

## 数据文件格式（表头即字段名，不可修改）

- **terms.csv 六列**：`阿语原文,中文译文,类别,领域,备注,处理方式`
  处理方式取值：`force_check`（强检查）/ `suggest`（推荐检查）/ `context_warning`（语境检查）
- **proper_names.csv 四列**：`阿语原文,中文译文,类别,备注`
- 编码 utf-8-sig（Excel 可开）；备注列不写逗号

## 关键设计约定

- **纯函数原则**：modules/ 下函数无 UI、无全局可变状态、路径作为参数传入；错误（如文件缺失）抛出由 app.py 转 st.error
- **阿语归一化（阶段 1 仅四项）**：去变音符、去 tatweel、去 bidi 控制符、أإآٱ→ا；**不做** ى→ي、ة→ه（防误配）、ؤ/ئ/ء（词义一部分）；字符集在 modules/glossary.py 的常量中，增删必须同步更新测试
- **匹配**：归一化后纯子串匹配，无词边界（命中派生词是已知取舍，如 فلسطين 命中 الفلسطينية）
- **段落切分**：有空行按空行分段；无空行按换行分段（每行一段）
- **RTL 显示**：阿语用 CSS class `.ar-para`（direction: rtl），只影响阿语区域；用户文本先 html.escape 再拼 HTML
- **session_state**：只用 1 个键 "results" 存结果，防止按钮后重跑丢失
- **翻译双模式（阶段 2）**：环境变量 `TRANSLATION_ENGINE` = mock（占位，默认）/ api（调阿里云）；api 缺密钥自动回退占位 + 页面黄色提示，不报错；逐段串行、一段失败中断整批（异常带段号）；术语/专名约束 `build_translation_constraints` **生成但不发送**（阿里云 TranslateGeneral 无 context 参数，payload 留 `params["Context"]` 注释位）；dotenv 只在 app.py 顶部加载一次，modules/ 不 import dotenv
- **配置集中管理（阶段 2 重构）**：翻译配置（环境变量名 `ENV_*` / 默认值 `DEFAULT_*` / 引擎标识 / `TranslationConfig` / `load_translation_config`）集中在 `modules/settings.py`，`translator.py` 只保留翻译逻辑；引用方**显式从正确模块导入**——配置符号从 settings 取，翻译符号（`translate_paragraphs` / `TranslationError` 家族）从 translator 取（异常刻意留 translator：错误是翻译行为的对外契约）；改配置只动 settings.py；环境变量值 strip、空串=未设置=默认值

## 开发规则（团队约定，必须遵守）

1. 分阶段开发：每次只实现一个阶段；输出前先说明本阶段目标，输出后说明修改了哪些文件
2. 模块化、可测试：每个函数 docstring 写清「作用 / 输入 / 输出」
3. 每个库方法/类调用前先写注释（用途、用法、参数含义）
4. **用户没有接触过 HTML**：涉及 HTML / CSS 的代码（如 st.markdown 的 unsafe_allow_html、`<style>` 样式、HTML 标签、CSS 属性）必须注释到「这是什么、每部分作用、为什么需要」的程度，不能只写一行带过
5. 需求不清楚先向用户提问，不擅自改技术路线
6. 分阶段目标参见 README.md「路线图」

## 当前阶段（第 2 阶段）已确认的决策

- 翻译引擎：requests 手写阿里云 RPC 签名（不引 SDK）；mock/api 双模式由环境变量切换
- 缺 key 回退占位（页面黄色提示），不崩溃；翻译失败（网络/业务/解析）中断整批并显示错误，旧结果保留
- 术语约束生成但不发送（阿里云该 API 无 context 参数）；DeepSeek key 仅 .env 预留（阶段 3 启用）
- 安全：真实密钥仅放本地 .env（gitignored），绝不进代码/README/测试/日志

阶段 1/2 已全部完成（本地流水线 + 双模式翻译 + 73 测试 + 文档）。详细决策见 docs/meeting_notes.md。
