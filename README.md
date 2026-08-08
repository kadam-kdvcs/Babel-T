# 阿拉伯语翻译审校助手（MVP）

面向**阿语政治外交 / 国际传播文本**的翻译审校辅助原型。用户粘贴一段阿语文本，系统切分段落、扫描术语库与专名库、生成译文（可接入阿里云机器翻译）与占位审校报告，为后续接入 LLM 审校打好框架。

## 当前阶段（第 2 阶段）

本阶段在阶段 1 纯本地流水线之上接入**真实翻译 API**（阿里云机器翻译，双模式）：

- 按段落切分文本（有空行按空行分段；无空行按换行分段）
- 扫描术语库 `data/terms.csv` 与专名库 `data/proper_names.csv`
- 阿语归一化（去变音符、去 tatweel、去 bidi 控制符、统一 أإآٱ→ا）后纯子串匹配
- **翻译双模式**：默认 `mock`（占位译文，不联网）；配置密钥后走 `api`（阿里云真实翻译，逐段串行）
- 显示：阿中双语对照、术语命中表、专名命中表、译文、占位审校报告

**双模式说明**：

| 模式 | 行为 | 何时用 |
|---|---|---|
| `mock`（默认） | 不联网，返回「（占位译文·第N段）待接入翻译 API」 | 开发调试、无密钥环境 |
| `api` | 调用阿里云机器翻译，返回真实译文 | 真实使用 |

模式由环境变量 `TRANSLATION_ENGINE` 控制（`mock` / `api`）。**api 模式未配置密钥时自动回退占位译文并在页面黄色提示**，不会报错崩溃；翻译失败（网络/业务/解析错误）页面显示错误、保留上次成功结果。

**本阶段明确不做**：LLM 审校、SQLite 存储、登录权限、部署、PDF/Word、词边界判断（纯子串会命中派生词，如「فلسطين」会命中「الفلسطينية」，属已知取舍）、术语约束发送给阿里云（该 API 无 context 参数，约束文本仅生成，供阶段 3 LLM 使用）。

## 目录结构

```
arabic-review-mvp/
├── app.py                      # Streamlit 页面（唯一 UI 入口）
├── requirements.txt            # streamlit / pytest / requests / python-dotenv
├── .env.example                # 环境变量模板（Key 留空，供复制为 .env）
├── .env                        # 本地真实密钥（gitignored，绝不提交）
├── CLAUDE.md                   # 给后续 Claude 会话的项目说明
├── data/
│   ├── terms.csv               # 术语库（六列，见下）
│   ├── proper_names.csv        # 专名库（四列，见下）
│   └── samples/politics_001.txt  # 示例阿语文本
├── modules/                    # 纯函数模块（不含 UI）
│   ├── segmenter.py            # 段落切分
│   ├── glossary.py             # 归一化 / CSV 加载 / 命中扫描
│   ├── translator.py           # 翻译双模式：mock 占位 / api 阿里云真实翻译
│   ├── reviewer.py             # 占位报告（阶段 3 接 LLM）
│   └── storage.py              # 占位（阶段 4 接 SQLite）
├── prompts/
│   └── review_report_prompt.md # LLM 审校提示词模板（阶段 3 启用）
├── docs/
│   ├── meeting_notes.md        # 开发决策记录
│   ├── modules.md              # 模块功能说明（供审查）
│   └── stage2_plan.md          # 阶段 2 实施计划（已批准）
└── tests/                      # pytest 单元测试
```

## 快速开始

```bash
# 1. 创建虚拟环境（Windows）
py -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. （可选）配置真实翻译：复制 .env.example 为 .env，填入阿里云 AccessKey
copy .env.example .env
#    然后编辑 .env：TRANSLATION_ENGINE=api + 填入 ALIYUN_ACCESS_KEY_ID/SECRET。
#    不配置也能跑：默认 mock 模式（占位译文，不联网）。
#    .env 已被 .gitignore 忽略，密钥绝不提交 git。

# 4. 启动页面（必须在项目根目录运行）
streamlit run app.py
```

打开浏览器进入页面后，输入框已预填示例文本，直接点「开始翻译与审校」即可看到效果。

**命令行直接调用流水线**（不启动页面）：`python -c "from app import run_pipeline; print(run_pipeline('文本')['translations'])"`（需在项目根目录；命令行环境同样会读取 .env 密钥）。

## 运行测试

```bash
# 必须在项目根目录运行（modules/ 不是包，靠当前目录被加入 sys.path）
python -m pytest -v
```

## 数据文件格式

两个 CSV 均为 **UTF-8（带 BOM，utf-8-sig）** 编码，Excel 可直接打开；表头不可修改（代码以表头名作为字段名）。**备注列约定不写逗号**（避免引号转义混乱）。

**terms.csv（六列）**

| 列 | 含义 | 示例 |
|---|---|---|
| 阿语原文 | 术语的阿语拼写 | السلام |
| 中文译文 | 术语的推荐中文译法 | 和平 |
| 类别 | 术语类别 | 政治 / 外交 / 国际关系 |
| 领域 | 该词条常见于哪类文章 | 政治新闻类 |
| 备注 | 补充说明 | 常用词 |
| 处理方式 | force_check（强检查）/ suggest（推荐检查）/ context_warning（语境检查） | force_check |

**proper_names.csv（四列）**：阿语原文、中文译文、类别（人名/地名/机构名）、备注。

## 阿语归一化规则（阶段 1）

匹配前对正文与词条都做归一化，只做四项：

1. 删除变音符（ً ٌ ٍ َ ُ ِ ّ ْ 及 U+0653~0655、U+0670）
2. 删除 tatweel 延长符 ـ
3. 删除 bidi 控制符（RLM/LRM/ALM 等，Word/微信粘贴常混入）
4. 统一字母变体：أ إ آ ٱ → ا

**刻意不做**：ى→ي、ة→ه（会引入误配）；ؤ / ئ（中位 hamza 是词义的一部分，如 مؤتمر「会议」、رئيس「总统」）。归一化字符集如需增删，必须同步更新 tests/test_glossary.py。

## 路线图

- **阶段 1（已完成）**：可运行页面 + 本地流水线 + 单测 + 文档
- **阶段 2（当前）**：接入真实翻译 API（阿里云机器翻译，双模式 mock/api，Key 走 `.env` 环境变量，绝不写入代码）
- **阶段 3**：LLM 生成审校报告（使用 prompts/review_report_prompt.md 模板；DeepSeek key 已在 .env 预留）
- **阶段 4**：SQLite 保存审校记录，老师审校意见输入与保存

## 开发规则（团队约定）

1. 分阶段开发：每次只实现一个阶段；输出前先说明本阶段目标，输出后说明修改了哪些文件
2. 模块化：每个函数尽量可测试，docstring 写清「作用 / 输入 / 输出」
3. 每个库方法 / 类调用前先写注释（用途、用法、参数含义）
4. API Key 绝不写入代码
5. 所有路径使用相对路径；所有文件读写显式指定 UTF-8 编码
6. 需求不清楚先提问，不擅自改动技术路线
