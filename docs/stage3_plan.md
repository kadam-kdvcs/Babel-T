# 阶段 3 实施计划：reviewer.py 接入 LLM 生成审校报告（可配置双模式 + 8 项报告结构）

> 状态：**已批准并实施完成**（2026-08-09 批准；同日实施完毕，99 测试全绿，四状态冒烟通过，Evaluator-Optimizer 双 Agent 协作）
> 对应计划文件：C:\Users\Lenovo\.claude\plans\python-encapsulated-noodle.md
> 实施纪要：docs/meeting_notes.md「2026-08-09 · 第 3 阶段需求确认与设计决策」

## Context（背景）

Tarjuman 项目已完成阶段 1（本地流水线）与阶段 2（阿里云机器翻译双模式）。第 3 阶段目标：**只升级 modules/reviewer.py**——把占位审校报告替换为可配置的 LLM 审校报告（DeepSeek / OpenAI 兼容接口），读取 prompts/review_report_prompt.md 模板，输出 8 项结构的 markdown 报告。不做 SQLite（阶段 4）、不改 segmenter/glossary/translator 核心。

**系统定位（用户明确强调）**：API 机器翻译是不可省略的主步骤；LLM 是**必不可少的校准辅助**——因为翻译 API 无法接入术语表（阶段 2 已知约束：阿里云 TranslateGeneral 无 context/术语参数），术语一致性必须由 LLM 在审校报告中把关；LLM 报告以真实翻译结果为审校依据。二者都执行，缺一不可。

项目根目录：`d:\G\python_coding\proj_vibe_coding\arabic-review-mvp\`

## 已确认决策（用户拍板，必须遵守）

1. **配置归属**：LLM 配置全部进 modules/settings.py（统一配置中心：新增 ReviewConfig + load_review_config，沿用 ENV_* 常量 / 空串=未设置=默认值 / 非法 engine 抛中文 ValueError / timeout 正整数校验模式；api_key 刻意不回落——空串是「缺凭证 → 回退占位」的判定依据，与 AK/SK 同策略）
2. **失败策略**（与阶段 2 翻译完全对称）：api 缺 key → 回退占位报告 + 页面黄色提示（review_fallback 标志）；调用失败（网络/业务/解析）→ 抛 reviewer 自定义异常家族（ReviewError 基类 → ReviewNetworkError/ReviewBusinessError/ReviewParseError，**放 reviewer.py**——错误是行为的对外契约）→ app.py st.error、旧结果保留（session_state 不覆盖机制已有，**api 翻译结果自然保留**）
3. **mock 模式报告**与阶段 1 占位文案**逐字节一致**（3 条旧测试零改动）
4. **DEEPSEEK_API_URL 是 base URL**（默认 https://api.deepseek.com，内部拼 /chat/completions，去重尾斜杠）
5. **开发调试视图按钮**（st.radio 两态，**默认全量视图**）：a) 仅 LLM 校准 = 只隐藏「双语对照」整节（术语/专名命中表仍显示——它们是审校依据）；b) LLM 校准 + API 翻译结果 = 全量。**按钮只影响 render_results 展示，不影响 run_pipeline 执行——翻译照常调用**
6. 请求用 requests 直调 OpenAI 兼容格式（**不引 openai SDK**）；temperature 固定 0.3，不配环境变量
7. 项目风格不变：纯函数原则、docstring「作用/输入/输出」、库方法调用前写注释、显式 UTF-8、教学式注释（用户没接触过 HTML）

## 文件改动清单

**新增 1 个**：`docs/stage3_plan.md`（即本文件）

**修改 9 个**：
| 文件 | 改动 |
|---|---|
| modules/settings.py | 新增 5 个 ENV_* 常量、4 个 DEFAULT_*、`ReviewConfig` frozen dataclass + `has_credentials`、`load_review_config()`；docstring 更新为「翻译与审校配置中心」；删除原预留注释行 |
| modules/reviewer.py | 整体重写（41 → 约 300 行）：异常家族 + 双模式编排 + 模板加载/占位符替换 + hits 格式化 + 请求构造/响应解析；公开签名 `generate_review_report(...) -> str` 不变 |
| prompts/review_report_prompt.md | 重写：`## 用户消息` 分隔标记（前=系统消息，后=用户消息）+ 8 项报告结构 + 三条核心约束 |
| app.py | run_pipeline 返回键 +2（review_mode/review_fallback）；except 链新增 `reviewer.ReviewError`；调试视图 st.radio（只影响渲染）；报告区渲染分支（st.markdown vs st.info）；caption 更新 |
| tests/test_reviewer.py | 保留 3 条旧测试（零改动）+ 新增约 26 条 |
| .env.example | 追加 4 个变量 + DEEPSEEK_API_KEY 占位注释转正式（9 → 13，计划原写 14 为计数笔误）；原 DEEPSEEK_API_KEY 占位注释转正式 |
| CLAUDE.md / README.md | 技术栈、目录结构、关键设计约定、当前阶段、路线图更新 |
| docs/modules.md / docs/meeting_notes.md / docs/dev_log.md | reviewer 节重写 + settings/app 节更新；阶段 3 决策小节；阶段 3 开发日志 |

**明确不动**：segmenter.py、glossary.py、translator.py（核心逻辑零改动）、storage.py、data/、requirements.txt（requests 已有，无新依赖）、.gitignore。

## 环境变量设计（追加到 settings.py 与 .env.example）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REVIEW_ENGINE` | `mock` | `mock`（占位报告，不联网，默认）/ `api`（调 LLM）；非法值抛 ValueError |
| `DEEPSEEK_API_KEY` | 空串 | LLM 审校凭证（api 必填；缺 key 自动回退占位）；**刻意不回落** |
| `DEEPSEEK_API_URL` | `https://api.deepseek.com` | base URL（无尾斜杠），程序自动拼 `/chat/completions`；兼容 OpenAI 格式的其他服务可覆盖 |
| `REVIEW_MODEL` | `deepseek-chat` | LLM 模型名 |
| `REVIEW_TIMEOUT_SECONDS` | `30` | 单请求超时，非法值抛 ValueError |

所有值读取后 `.strip()`；空串 = 未设置 = 默认值（api_key 除外）；dotenv 仍只在 app.py 顶部加载。

## reviewer.py 函数契约

模块级私有常量：`_PROMPT_PATH`（项目根/prompts/review_report_prompt.md）、`_PLACEHOLDERS`（4 个占位符）、`_USER_SECTION_MARKER = "## 用户消息"`、`_TEMPERATURE = 0.3`。

**异常体系（全部中文消息，绝不包含任何密钥）**：
- `ReviewError(Exception)`：基类
- `ReviewNetworkError`：超时 / 连接失败
- `ReviewBusinessError`：HTTP 非 200，附 status_code 与响应片段（text[:200] 截断）
- `ReviewParseError`：非 JSON / 缺 choices / 缺 message.content

**公开函数（签名不变）**：
- `generate_review_report(paragraphs, translations, term_hits, name_hits) -> str`：入口。① 空输入短路 → 占位报告（含「0 段」统计），**api 模式同样不发请求**（与阶段 2「api 空输入不发请求」契约对称）；② `config = load_review_config()`；③ `engine == MOCK_ENGINE or not config.has_credentials` → `_generate_mock_report`（占位文案与阶段 1 逐字节一致）；④ 否则 `_review_with_api(...)`

**私有函数**：
| 函数 | 说明 |
|---|---|
| `_generate_mock_report` | 占位文案逐字节复制阶段 1（统计口径 `sum(hit.get("count", 1))` 原样） |
| `_load_prompt_template()` | 读模板（UTF-8）；缺文件抛 FileNotFoundError（app.py 已捕获）；结构校验：4 占位符各恰好 1 次 + 含「## 用户消息」标记，否则中文 ValueError |
| `_format_hits_text(term_hits, name_hits)` | 两库统一格式化（.get 兼容键差异——专名无「领域」「处理方式」）：`【术语命中】共 N 条\n- 阿语原文「…」→ 中文译文「…」（类别：…；领域：…；备注：…；处理方式：…；出现段落：1, 4, 5；出现次数：7）`；无命中输出「无」；处理方式保留 CSV 原始值（语义说明放模板，不在代码里复制 UI 映射） |
| `_format_numbered(items, label)` | 段落/译文编号化「第1段：…」，让 LLM 能引用段号 |
| `_render_user_message(template, ...)` | 按「## 用户消息」split，只对用户段做 4 次 `str.replace`（`f"{{{name}}}"` → 文本）——**选 replace 不用 format_map**（对模板内中文括号零约束）；split 不足 2 段 → ValueError |
| `_build_api_url(config)` | `config.api_url.rstrip("/") + "/chat/completions"`（去重尾斜杠） |
| `_build_request_payload(config, system, user)` | `{"model": ..., "messages": [{role: system}, {role: user}], "temperature": 0.3}` |
| `_review_with_api(...)` | 编排：模板 → split → 格式化 → replace → `requests.post(url, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=config.timeout_seconds)` → `_parse_review_response` |
| `_parse_review_response(response)` | 错误分类（见下）；成功返回 `data["choices"][0]["message"]["content"]`（逐层 .get 校验） |

## prompt 模板设计（重写 prompts/review_report_prompt.md）

结构：`# 标题 + 说明` → `## 系统消息`（角色设定 + 【输出结构】8 项 + 【约束】3 条）→ `## 用户消息`（数据说明 + 4 占位符）。

**8 项输出结构**（每项标题 + 一句「该部分做什么」的指示，LLM 每次固定遵守）：
1. 文本概况：段落数、文本规模，依据原文判断文本类型与主题
2. 术语命中与风险：逐条核查译文是否与术语库命中一致，标注风险等级
3. 专名命中与统一性：专名译法是否统一、是否与库中译法一致
4. 重点风险句段：逐段列出需重点复核的句段（段号 + 原文引文 + 译文 + 问题 + 置信度）
5. 语体与领域适配：政治外交场合的正式度与语域评价
6. 文化/政治语境提醒：**仅基于原文**，提醒文化/政治敏感表达
7. 总体结论：整体质量评价
8. 人工复核建议：给审校人员的具体复核清单

**三条核心约束**（体现「不重翻全文 / 不添加信息 / 术语以术语库为准」）：
- 只审校给定的译文，**绝不重写全文、绝不重新翻译**
- **不添加原文与术语库中不存在的信息**；不确定处标注置信度（高/中/低）
- **术语翻译以术语库为准**；处理方式语义（force_check=强检查 / suggest=推荐检查 / context_warning=语境检查）写入模板系统消息

## 错误处理策略

| 失败类型 | 行为 | 页面表现 |
|---|---|---|
| 缺 key（api 无凭证） | 不抛，回退占位 | st.warning「已配置 LLM 校准 API 模式但未配置密钥，本次使用占位报告」 |
| 超时 / 连接失败 | ReviewNetworkError | st.error「审校失败：…」，旧结果保留（翻译结果不丢） |
| HTTP 非 200 | ReviewBusinessError（含状态码 + 响应片段 text[:200]） | 同上 |
| 响应非 JSON | ReviewParseError | 同上 |
| 200 但缺 choices/message/content | ReviewParseError | 同上 |
| 环境变量非法 | ValueError（配置错误） | st.error「配置错误：…」 |
| 模板缺失 / 结构非法 | FileNotFoundError / ValueError | st.error（数据文件缺失 / 配置错误） |

**run_pipeline 返回结构扩展**：新增两键 `"review_mode": str`（"mock"/"api"）、`"review_fallback": bool`（本次是否回退占位），与 translation_mode/translation_fallback 对称。旧 7 键不变（兼容）。

## app.py 改动要点

1. **run_pipeline**：`generate_review_report(...)` 调用后追加 `review_config = settings.load_review_config()`、`review_mode`、`review_fallback`（与翻译配置块对称）；返回 dict 共 9 键
2. **except 链**（按业务发生顺序）：FileNotFoundError → translator.TranslationError → **reviewer.ReviewError**（st.error「审校失败：{e}」）→ ValueError
3. **调试视图 radio**（按钮块之后、渲染块之前）：
   ```python
   view_mode = st.radio("开发调试：审校视图（只影响下方展示；翻译与审校照常执行）",
       ("仅 LLM 校准（审校报告）", "LLM 校准 + API 翻译结果（双语对照与译文区）"),
       horizontal=True, index=1, key="review_view")
   ```
   `render_results(results, show_translations=(view_mode == 全量))`；`show_translations=False` 时跳过「双语对照」整节（术语/专名命中表仍显示）；附 caption「提示：翻译 API 是不可省略的步骤，本按钮只切换展示区，run_pipeline 照常执行。」
4. **报告区渲染**：review_fallback → st.warning；api 真报告 → `st.markdown(results["report"])`（LLM 输出是 markdown）+ caption「以上审校报告由 LLM 生成，仅供人工复核参考；请以原文与术语库为准。」；mock/回退 → 保留 `st.info` 占位样式
5. **caption + 模块 docstring** 更新为阶段 3 说明

## 测试计划（tests/test_reviewer.py，预计约 29 条；全项目 99 条）

**通用约定**：不 import app.py；所有 key 用假值（`monkeypatch.setenv` REVIEW_ENGINE=api + DEEPSEEK_API_KEY=fake_key）；mock 请求用 `monkeypatch.setattr(reviewer.requests, "post", fake_post)` + 假响应对象（status_code / .json() / text）；模板用 `monkeypatch.setattr(reviewer, "_PROMPT_PATH", tmp_path/"prompt.md")` 指向测试模板；**所有测试不打真实网络、不出现真实 key**。

**保留 3 条**：统计数字 / 占位标记 / 返回类型（零改动；REVIEW_ENGINE 缺省即 mock → 走占位路径，断言天然成立）

**新增 ~26 条**：
- 配置（7）：未设置 → 默认值 / 设置 + strip / 非法 engine 抛 ValueError / engine 空串回落 mock / timeout 非整数抛错 / timeout 空串回落默认 / URL 空串回落默认 + api_key 空串不回落（has_credentials=False）
- 双模式（4）：mock 不联网（post 零调用）/ api 缺 key 回退占位（文案与 mock 逐字节相同）/ api 空输入不发请求 / api 有 key 成功（恰好 1 次 post）
- 模板（4）：替换正常（body 含「第1段：」编号文本，不含 `{source_paragraphs}` 字面量）/ 模板缺失 FileNotFoundError / 缺占位符 ValueError / 缺「## 用户消息」标记 ValueError
- hits 格式化（2）：术语六列键全格式化（含「出现段落：1, 4, 5」「出现次数」）/ 专名四列键不抛 KeyError / 全空输出「无」
- 请求构造（3）：URL 去尾斜杠拼接 / body 结构（model、messages 双角色、temperature==0.3、**body 文本不含 api_key** 安全断言）/ headers Bearer + Content-Type + timeout
- 错误路径（6）：HTTP 401 → Business（含状态码与片段、不含 key）/ Timeout → Network / ConnectionError → Network / 非 JSON → Parse / 缺 choices → Parse / 缺 message.content → Parse

## 不做事项清单

1. 不改 segmenter.py / glossary.py / translator.py 核心逻辑；不接 SQLite、storage.py 不动
2. 不做登录、部署、PDF/Word、高亮、词边界规则
3. 不做并发/批处理、失败重试、流式输出（单请求、单次尝试）
4. 不让 LLM 重翻全文（模板约束 + 8 项结构限定「指出问题」而非产出新译文）
5. 不让 LLM 添加原文没有的信息（模板约束 + 第 6 项限定「仅基于原文」）
6. 不把任何 Key 写入代码、README、测试、日志、错误消息（业务错误片段截断 200 字符）
7. 不引入 openai SDK / LangChain / pandas / FastAPI / Dify / React
8. 不在页面上提供引擎切换控件（REVIEW_ENGINE 由环境变量决定，同 TRANSLATION_ENGINE 决策）
9. 不把术语/专名命中发送给翻译请求（translator payload 的 Context 注释位仍保持注释）
10. temperature 固定 0.3，不配环境变量；不新增其他环境变量
11. 不缓存、不持久化 LLM 报告

## 实现顺序

1. settings.py 扩展（常量 + ReviewConfig + load_review_config + docstring）→ 配置 7 条测试绿
2. prompts/review_report_prompt.md 重写（纯内容，无代码依赖）
3. reviewer.py 重写：a) 异常家族 + `_generate_mock_report`（旧 3 条测试立即仍绿）→ b) 模板加载 + 占位符替换 + hits 格式化（纯函数可单测）→ c) 请求构造 + 解析 + 编排
4. test_reviewer.py 全量新增，`python -m pytest -v` 全量跑（预期 99 条）
5. app.py（返回键 + except 链 + radio + 报告渲染 + caption）
6. .env.example + 文档四件套（CLAUDE.md / README.md / meeting_notes.md / modules.md / dev_log.md）+ stage3_plan.md 状态更新
7. 手动冒烟四状态（真实 .env）：mock 占位 / api 缺 key 回退+黄条 / api 真 key 8 项报告 / api 假 key st.error「审校失败」旧结果保留；radio 两种视图切换
8. 安全核查：grep 代码/测试/文档无真实密钥

## 验证方式

1. `python -m pytest -v` 全绿（73 + ~26 = 99 条；旧测试零改动）
2. `python -c` 直接调 run_pipeline：mock 返回占位报告；api（配真 key）返回 LLM 8 项报告；缺 key 返回占位 + review_fallback=True
3. `streamlit run app.py`（**需重启 server**）：
   - mock：占位报告，无警告
   - api + 真 key：8 项 markdown 报告渲染正常；radio 切「仅 LLM 校准」后双语对照区隐藏、术语/专名表仍显示
   - api 缺 key：占位报告 + 黄色警告
   - api + 假 key：st.error「审校失败」，页面不崩，翻译结果与旧结果保留
4. 检查终端日志无任何密钥输出

## 安全注意事项

- 真实 DeepSeek key 仅写入本地 .env（gitignored），绝不进代码/README/测试/日志/错误消息
- 用户密钥曾在聊天中暴露，联调验证通过后建议在阿里云/DeepSeek 控制台**轮换重置**
- 错误消息与异常不含密钥；业务错误只带响应片段前 200 字符

---

## 待商榷问题（讨论区，随讨论更新）

- 2026-08-09 用户明确三处定位，已写入决策：
  1. **「LLM 是校准辅助，api 翻译不可忽略」**：翻译与 LLM 审校都执行；LLM 报告必须以真实翻译结果为审校依据（翻译 API 无法接入术语表，术语一致性靠 LLM 把关）
  2. **失败策略**：「LLM 调用失败时应保留 api 机器翻译的结果」→ 对应「调用失败抛异常 + session_state 不覆盖（旧结果保留）」机制，翻译结果区不丢失
  3. **开发调试按钮**：切换「仅 LLM 校准 / LLM 校准 + API 翻译结果」两种视图，只影响展示不影响执行
