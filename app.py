"""Streamlit 页面入口（第 3.2 阶段）：阿拉伯语翻译审校助手 MVP。

页面布局：
1. 标题与说明
2. 阿语文本输入框（预填示例文本）
3. 「开始翻译与审校」按钮
4. 展示区：四结果对比 / 术语命中 / 专名命中 / 审校报告

翻译双模式（阶段 2）：默认 mock（占位译文，不联网）；配置阿里云
密钥后走 api（真实翻译）。api 缺密钥时自动回退占位译文并提示。

LLM 多结果（阶段 3 / 3.1 / 3.2）：默认 mock（占位，不联网）；
配置 DeepSeek 密钥后走 api（读取 prompts/ 下三份独立模板，分三次
独立调用：直接翻译原文（不带 API 译文）、基于 API 译文修正、
结合原文仲裁最终结果并输出取舍说明与 8 项审校报告）。
最终仲裁以原文为最高依据，禁止添加原文未有的信息。api 缺密钥时
自动回退占位并提示。任一异常时页面提示错误、保留上次成功结果（翻译结果不丢）。

四结果对比（阶段 3.2）：页面固定显示四个翻译结果——原始 API 译文、
LLM 直接翻译结果、LLM 修正结果、LLM 最终结果（以原文为准），
术语/专名命中表与审校报告始终保留。

本文件是唯一包含 UI 的模块；流水线逻辑集中在 run_pipeline()，
不依赖页面状态，可在命令行直接调用验证。
"""

import html  # 标准库：转义用户文本，防止 HTML 注入
import json  # 标准库：读取/确认 L2/L3 JSON 内容
from collections.abc import Callable  # 标准库：类型标注用（流水线回调）
from pathlib import Path  # 标准库：跨平台路径处理

import streamlit as st  # 页面框架：所有界面控件都来自这里

# python-dotenv：把项目根目录 .env 文件里的键值对加载进环境变量。
# 原因：翻译 API 的密钥（AccessKey 等）绝不能写死在代码里，而是由用户
# 填写在 .env 文件中（该文件已被 .gitignore 忽略，绝不会提交 git），
# 程序启动时用 load_dotenv 把密钥读进环境变量，供 translator 模块读取。
# 注意：只有 app.py 在这里加载 .env；modules/ 下不 import dotenv，
# 保证翻译模块在测试中只依赖注入的环境变量（可测试性）。
from dotenv import load_dotenv

# 业务模块：段落切分 / 词库扫描 / 翻译（双模式）/ 占位报告；
# settings 是翻译配置中心（环境变量名/默认值/配置加载），
# 配置符号从这里取，翻译符号（translate_paragraphs、异常）从 translator 取
from modules import glossary, reviewer, segmenter, settings, storage, translator

# 项目根目录：本文件所在目录（无论从哪个目录启动 streamlit 都有效）
BASE_DIR = Path(__file__).parent

# load_dotenv(BASE_DIR / ".env")：显式指定 .env 的路径为「项目根目录」，
# 保证无论从哪个目录启动 streamlit（哪怕从别的目录敲命令），
# 都能找到 .env 文件并读取其中的密钥配置。
# 该加载在 app.py 顶部执行一次，之后各模块读取 os.environ 即可。
load_dotenv(BASE_DIR / ".env")

# 处理方式代码 → 中文显示标签（仅展示用，不改动 CSV 里存的值）
_CHECK_LABELS = {
    "force_check": "强检查",
    "suggest": "推荐检查",
    "context_warning": "语境检查",
}

# 术语命中表要展示的列（terms.csv 六列表头 + 扫描补充的两列）
TERM_COLUMNS = ["阿语原文", "中文译文", "类别", "领域", "备注", "处理方式", "段落", "出现次数"]
# 专名命中表要展示的列（proper_names.csv 四列表头 + 扫描补充的两列）
NAME_COLUMNS = ["阿语原文", "中文译文", "类别", "备注", "段落", "出现次数"]

# 页面展示结果为“四结果固定对比”：原始 API 译文 / LLM 直接翻译 /
# LLM 基于 API 译文修正 / LLM 最终仲裁结果。保留常量位供未来如需
# 切换视图时使用。
VIEW_COMPARISON = "四结果对比"


def _load_sample() -> str:
    """读取示例文本，供输入框首次渲染预填。

    作用：新手打开页面即有内容可点，不需要自己找阿语文本。
    输入：无。
    输出：str —— 示例文本全文；文件缺失时返回空串。
    """
    sample_path = BASE_DIR / "data" / "samples" / "politics_001.txt"
    if sample_path.exists():  # Path.exists：判断文件是否存在
        # Path.read_text：一次性读整个文件，显式指定 UTF-8（Windows 默认 GBK）
        return sample_path.read_text(encoding="utf-8")
    return ""


def run_pipeline(text: str) -> dict:
    """执行完整处理流水线（切分 → 扫描 → 翻译 → 报告）。

    作用：命令行/测试/无 UI 环境的完整入口。实现上委托给
          run_pipeline_progressive（不带任何回调）：翻译段落在 api 模式下
          也会并发执行，缩短多段文本的等待时间。
    输入/输出/异常：同 run_pipeline_progressive。
    """
    return run_pipeline_progressive(text)


def run_pipeline_progressive(
    text: str,
    *,
    on_paragraphs: Callable[[list[str]], None] | None = None,
    on_translation: Callable[[int, str], None] | None = None,
    on_review_start: Callable[[], None] | None = None,
    on_review_done: Callable[[dict], None] | None = None,
    db_path: Path | None = None,
) -> dict:
    """执行完整处理流水线，并支持“逐段完成即回调”与 SQLite 持久化。

    作用：
      - 同旧版一样返回完整结果；
      - 在任务开始后创建 document / paragraphs / translation_run；
      - API 每段完成后立即保存 api_translation；
      - LLM 直接翻译、修正、最终仲裁每步完成后立即保存；
      - 任一阶段失败时更新 run 状态并保留已保存结果。
    输入：text —— 原文；回调均可选；db_path —— 可选的数据库路径。
    输出：dict —— 与阶段 3.2 相同的结果结构。
    """
    # 段落切分：整篇文本 → 段落列表
    paragraphs = segmenter.segment_paragraphs(text)
    if on_paragraphs is not None:
        on_paragraphs(paragraphs)

    # ---- 持久化：创建文档、段落、运行记录 ----
    run_id = None
    paragraph_ids: list[int] = []
    saved_any = False
    if paragraphs:
        document_id = storage.create_document(
            db_path,
            title="翻译任务",
            raw_text=text,
            source_lang="ar",
            target_lang="zh",
        )
        paragraph_ids = storage.save_paragraphs(db_path, document_id, paragraphs)
        run_id = storage.create_translation_run(
            db_path,
            document_id,
            translation_engine=settings.load_translation_config().engine,
            review_engine=settings.load_review_config().engine,
            translation_model=settings.load_translation_config().model if hasattr(settings.load_translation_config(), "model") else "",
            review_model=settings.load_review_config().model,
        )
        # 现在 run_id 已知，初始化 pending 记录
        for pid in paragraph_ids:
            storage.save_paragraph_result(
                db_path, run_id, pid, status="pending"
            )

    # 包装 on_translation：每段 API 翻译完成后保存
    def _persist_translation(index: int, translation: str) -> None:
        nonlocal saved_any
        saved_any = True
        if on_translation is not None:
            on_translation(index, translation)
        if run_id is not None:
            storage.save_paragraph_result(
                db_path,
                run_id,
                paragraph_ids[index],
                api_translation=translation,
                status="completed",
            )

    try:
        # 加载术语库与专名库
        terms = glossary.load_glossary(BASE_DIR / "data" / "terms.csv")
        names = glossary.load_glossary(BASE_DIR / "data" / "proper_names.csv")
        term_hits = glossary.scan_glossary(paragraphs, terms)
        name_hits = glossary.scan_glossary(paragraphs, names)

        # 翻译：mock 占位 / api 并发翻译
        translations = translator.translate_paragraphs_parallel(
            paragraphs,
            term_hits,
            name_hits,
            on_translation=_persist_translation,
        )

        # 翻译模式与回退标志
        config = settings.load_translation_config()
        translation_mode = config.engine
        translation_fallback = (
            config.engine == settings.API_ENGINE and not config.has_credentials
        )

        # mock/回退模式不会触发 on_translation，这里补一次保存，保证历史里有 API 译文
        if run_id is not None and (translation_mode == settings.MOCK_ENGINE or translation_fallback):
            for idx, trans in enumerate(translations):
                storage.save_paragraph_result(
                    db_path,
                    run_id,
                    paragraph_ids[idx],
                    api_translation=trans,
                    status="completed",
                )

        # LLM 多轮处理前通知 UI
        if on_review_start is not None:
            on_review_start()

        # 第一步：LLM 直接翻译
        direct_result = reviewer.generate_direct_translations(
            paragraphs, term_hits, name_hits, fallback_translations=translations
        )
        if run_id is not None:
            for pid, trans in zip(paragraph_ids, direct_result["translations"]):
                storage.save_paragraph_result(
                    db_path, run_id, pid, llm_direct_translation=trans
                )

        # 第二步：基于 API 译文修正
        corrected_result = reviewer.generate_corrected_translations(
            paragraphs, translations, term_hits, name_hits
        )
        if run_id is not None:
            for pid, trans in zip(paragraph_ids, corrected_result["translations"]):
                storage.save_paragraph_result(
                    db_path, run_id, pid, llm_corrected_translation=trans
                )

        # 第三步：最终仲裁
        final_result = reviewer.generate_final_arbitration(
            paragraphs,
            direct_result["translations"],
            corrected_result["translations"],
            term_hits,
            name_hits,
        )
        if run_id is not None:
            for pid, trans in zip(paragraph_ids, final_result["final_translations"]):
                storage.save_paragraph_result(
                    db_path, run_id, pid, llm_final_translation=trans
                )
            storage.update_run_status(
                db_path,
                run_id,
                "completed",
                tradeoff_notes=final_result.get("tradeoff_notes", ""),
                report=final_result.get("report", ""),
                completed=True,
            )

        # 完整结果包
        review_result = {
            "report": final_result.get("report", ""),
            "direct_translations": direct_result["translations"],
            "corrected_translations": corrected_result["translations"],
            "final_translations": final_result.get("final_translations", []),
            "tradeoff_notes": final_result.get("tradeoff_notes", ""),
            "direct_degraded": direct_result.get("degraded", False),
            "corrected_degraded": corrected_result.get("degraded", False),
            "final_degraded": final_result.get("final_degraded", False),
            "tradeoff_degraded": final_result.get("tradeoff_degraded", False),
        }
        if on_review_done is not None:
            on_review_done(review_result)
        return {
            "paragraphs": paragraphs,
            "translations": translations,
            "direct_translations": review_result["direct_translations"],
            "corrected_translations": review_result["corrected_translations"],
            "final_translations": review_result["final_translations"],
            "tradeoff_notes": review_result["tradeoff_notes"],
            "direct_degraded": review_result["direct_degraded"],
            "corrected_degraded": review_result["corrected_degraded"],
            "final_degraded": review_result["final_degraded"],
            "tradeoff_degraded": review_result["tradeoff_degraded"],
            "term_hits": term_hits,
            "name_hits": name_hits,
            "report": review_result["report"],
            "translation_mode": translation_mode,
            "translation_fallback": translation_fallback,
            "review_mode": settings.load_review_config().engine,
            "review_fallback": (
                settings.load_review_config().engine == settings.API_ENGINE
                and not settings.load_review_config().has_credentials
            ),
        }
    except Exception as e:
        # 任一阶段失败：保留已保存结果，更新运行状态为 partial/failed 后继续抛出
        if run_id is not None:
            status = "partial" if saved_any else "failed"
            try:
                storage.update_run_status(
                    db_path, run_id, status, error_message=str(e)
                )
            except Exception:  # noqa: BLE001
                pass
        raise


def _hits_to_rows(hits: list[dict], columns: list[str]) -> list[dict]:
    """把扫描命中列表转成展示行（统一列名与列序）。

    作用：命中 dict 的键是 CSV 表头名（如「阿语原文」「paragraph_no」），
          这里转成页面要展示的中文列名并固定列序，供 st.dataframe 渲染。
    输入：hits —— scan_glossary 的输出；columns —— 要展示的列名列表。
    输出：list[dict] —— 每个 dict 的键按 columns 的顺序排列。
    """
    rows = []
    for hit in hits:
        # 处理方式显示标签映射（取不到映射时保留原值）
        check_value = hit.get("处理方式", "")
        check_label = _CHECK_LABELS.get(check_value, check_value)
        row = {
            "阿语原文": hit.get("阿语原文", ""),
            "中文译文": hit.get("中文译文", ""),
            "类别": hit.get("类别", ""),
            "领域": hit.get("领域", ""),
            "备注": hit.get("备注", ""),
            "处理方式": check_label,
            # 出现段落列表（如 [1, 4, 5]）转成 "1, 4, 5" 字符串展示
            "段落": ", ".join(str(p) for p in hit.get("paragraphs", [])),
            "出现次数": hit.get("count", ""),
        }
        # 只保留 columns 里要求的列，保证列序一致
        rows.append({col: row[col] for col in columns})
    return rows


def render_results(results: dict) -> None:
    """渲染结果展示区（四结果对比 / 术语命中 / 专名命中 / 审校报告）。

    作用：把 run_pipeline 的结果渲染到页面。翻译结果对比区固定展示四个结果：
          1. 原始 API 译文
          2. LLM 直接翻译原文的结果
          3. LLM 基于 API 译文修正后的结果
          4. LLM 结合原文与上面两者仲裁出的最终结果
          最终仲裁以原文为准，不添加原文未有的信息。审校报告作为辅助
          信息始终保留在下方。
    输入：results —— run_pipeline 的输出 dict。
    输出：无（直接向页面输出控件）。
    """
    # 回退提示：配置了 api 模式但没配密钥（run_pipeline 已判定回退），
    # 本次译文是占位内容。提示放在结果区顶部，让用户第一眼看到，
    # 而不是在每段译文旁边重复解释。
    if results.get("translation_fallback"):
        st.warning("已配置 API 模式但未配置密钥，本次使用占位译文。")
    if results.get("review_fallback"):
        st.warning("已配置 LLM 校准 API 模式但未配置密钥，本次使用占位 LLM 结果与占位报告。")

    st.subheader("翻译结果对比（四个结果）")
    st.caption("最终结果以原文为最高依据进行仲裁，不添加原文未有的信息。")

    # 兼容旧结果：如果 session_state 里缺少某个列表（旧版结果），回退用原始译文
    translations = results.get("translations", [])
    direct_translations = results.get("direct_translations", translations)
    corrected_translations = results.get("corrected_translations", translations)
    final_translations = results.get("final_translations", corrected_translations)

    # zip：把段落与四个译文一一配对；enumerate：从 1 开始编号段落
    for i, (paragraph, raw_translation, direct_translation, corrected_translation, final_translation) in enumerate(
        zip(
            results["paragraphs"],
            translations,
            direct_translations,
            corrected_translations,
            final_translations,
        ),
        start=1,
    ):
        st.markdown(f"**第 {i} 段（阿语）**")
        # ---- 下面这行同时涉及 HTML 转义与换行处理，说明如下 ----
        # html.escape(paragraph)：把用户文本里的 < > & 等特殊字符转成安全写法
        #   （如 < 变成 &lt;）。原因：这些字符如果原样进入页面，会被浏览器当成
        #   网页代码执行——用户故意输入 <script> 就能注入脚本，这叫「注入攻击」。
        #   escape 之后它们只会被当作普通文字显示，页面结构不受用户输入影响。
        # .replace("\n", "<br>")：把段落内部的换行符换成 <br> 标签。
        #   <br> 是 HTML 里的「换行」标签（break line 的缩写，写成 <br> 即换行）；
        #   浏览器默认会把连续空白（含换行）压缩成一个空格，
        #   所以不转成 <br> 的话，段内换行显示时会消失，整段挤成一行。
        safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
        # <div> 是 HTML 的「分区」标签，表示一块独立的区域；
        # class="ar-para" 给这块区域贴上名为 ar-para 的类标签，
        # 于是上面 <style> 里的 .ar-para 规则就会只作用于这个元素（阿语区）。
        # 注意顺序：必须先 escape 消毒、再套 <div> 标签，
        # 保证用户输入里的尖括号不会破坏 div 标签本身的结构。
        st.markdown(f'<div class="ar-para">{safe_paragraph}</div>', unsafe_allow_html=True)

        # ---- 四个结果统一渲染，减少重复代码 ----
        def _render_one(label: str, text: str, source_is_api: bool) -> None:
            """渲染一个译文结果块；source_is_api 决定是否带 API 占位标签。"""
            if source_is_api:
                # 原始 API 译文，可能因缺密钥回退占位
                if results.get("translation_mode") == settings.API_ENGINE and not results.get(
                    "translation_fallback"
                ):
                    st.markdown(f"**第 {i} 段（{label}）**")
                else:
                    st.markdown(f"**第 {i} 段（{label}·占位）**")
            else:
                # LLM 三个结果，可能因缺密钥回退占位
                if results.get("review_mode") == settings.API_ENGINE and not results.get(
                    "review_fallback"
                ):
                    st.markdown(f"**第 {i} 段（{label}）**")
                else:
                    st.markdown(f"**第 {i} 段（{label}·占位）**")
            st.markdown(
                f'<div class="zh-trans">{html.escape(text)}</div>',
                unsafe_allow_html=True,
            )

        _render_one("原始 API 译文", raw_translation, True)
        _render_one("LLM 直接翻译结果", direct_translation, False)
        _render_one("LLM 修正结果", corrected_translation, False)
        _render_one("LLM 最终结果（以原文为准）", final_translation, False)
        st.divider()

    # 翻译取舍说明：展示 LLM 在最终仲裁时对两个候选版本的取舍与原因
    tradeoff_notes = results.get("tradeoff_notes", "")
    if tradeoff_notes:
        st.subheader("翻译取舍说明")
        st.markdown(tradeoff_notes)

    st.subheader("术语命中")
    term_rows = _hits_to_rows(results["term_hits"], TERM_COLUMNS)
    if term_rows:
        # st.dataframe：把 list of dict 渲染成表格（内部经 pyarrow 转换，无需 pandas）
        st.dataframe(term_rows)
    else:
        st.caption("未命中任何术语。")

    st.subheader("专名命中")
    name_rows = _hits_to_rows(results["name_hits"], NAME_COLUMNS)
    if name_rows:
        st.dataframe(name_rows)
    else:
        st.caption("未命中任何专名。")

    st.subheader("审校报告")
    # review_fallback 的黄色提示已在结果区顶部统一展示，这里不再重复。
    if results.get("review_mode") == settings.API_ENGINE and not results.get(
        "review_fallback"
    ):
        # api 真报告：LLM 输出的本身就是 markdown 文本（含 ## 标题、
        # - 列表、**加粗** 等标记），st.markdown 会把它解析成页面上的
        # 富文本格式（标题变大、列表自动缩进等）。
        # 这里故意不传 unsafe_allow_html=True：Streamlit 默认把字符串里
        # 的 HTML 标签（如 <script>、<div>）当作普通文字转义显示、不执行。
        # 报告文本来自 LLM，万一它输出了恶意标签，也不会在页面里生效，
        # 这样更安全；代价是报告里若有真 HTML 会以原样文本显示，不影响
        # 阅读审校内容。
        st.markdown(results["report"])
        st.caption("以上审校报告由 LLM 生成，仅供人工复核参考；请以原文与术语库为准。")
    else:
        # mock 模式或回退占位：报告是纯占位文本，用 st.info 灰底信息框展示
        st.info(results["report"])



def render_history() -> None:
    """渲染历史记录区：选择运行、查看详情、保存人工译文与审校意见。"""
    st.subheader("历史记录")
    try:
        runs = storage.list_runs(None)
    except Exception as e:  # noqa: BLE001
        st.error(f"读取历史记录失败：{e}")
        return
    if not runs:
        st.caption("暂无历史记录。")
        return

    options = {r["id"]: f"#{r['id']} —— {r['status']} —— {r['started_at']}" for r in runs}
    selected_id = st.selectbox("选择历史运行", list(options.keys()), format_func=lambda x: options[x])
    details = storage.get_run_details(None, selected_id)
    if details is None:
        st.warning("找不到该运行记录。")
        return

    st.markdown(f"**状态：** {details['run']['status']}")
    if details["run"].get("error_message"):
        st.warning(f"错误信息：{details['run']['error_message']}")
    if details["run"].get("tradeoff_notes"):
        st.markdown("**翻译取舍说明：**")
        st.markdown(details["run"]["tradeoff_notes"])
    if details["run"].get("report"):
        st.markdown("**审校报告：**")
        st.markdown(details["run"]["report"])

    # ---- 段落详情与人工译文 ----
    st.markdown("### 段落结果与人工最终译文")
    for p in details["paragraphs"]:
        st.markdown(f"**第 {p['paragraph_index'] + 1} 段（阿语）**")
        st.write(p["source_text"])
        st.caption(
            f"API：{p.get('api_translation') or '无'} ｜ "
            f"直接：{p.get('llm_direct_translation') or '无'} ｜ "
            f"修正：{p.get('llm_corrected_translation') or '无'} ｜ "
            f"最终：{p.get('llm_final_translation') or '无'} ｜ "
            f"人工：{p.get('human_final_translation') or '无'}"
        )
        current_value = p.get("human_final_translation") or p.get("llm_final_translation") or ""
        human_text = st.text_area(
            f"人工译文（第 {p['paragraph_index'] + 1} 段）",
            value=current_value,
            key=f"human_{selected_id}_{p['paragraph_id']}",
        )
        if st.button("保存人工译文", key=f"save_human_{selected_id}_{p['paragraph_id']}"):
            storage.save_human_translation(None, selected_id, p["paragraph_id"], human_text)
            st.success("已保存人工译文。")

        # 分段审校意见
        para_note_content = st.text_area(
            f"分段审校意见（第 {p['paragraph_index'] + 1} 段）",
            key=f"para_note_{selected_id}_{p['paragraph_id']}",
        )
        if st.button("保存分段审校意见", key=f"save_para_note_{selected_id}_{p['paragraph_id']}"):
            if para_note_content.strip():
                storage.save_review_note(
                    None,
                    selected_id,
                    para_note_content,
                    note_type="other",
                    paragraph_id=p["paragraph_id"],
                )
                st.success("已保存分段审校意见。")
            else:
                st.warning("分段意见内容不能为空。")

        # ---- 教师审校工作流（L1 → L2 → L3）----
        st.markdown(f"#### 教师审校：第 {p['paragraph_index'] + 1} 段")
        draft_translation = p.get("llm_final_translation") or p.get("human_final_translation") or ""
        latest_record = storage.get_latest_review_record(
            None, selected_id, p["paragraph_id"]
        )
        if latest_record is None:
            # L1 提交表单
            teacher_decision_label = st.radio(
                "审校结论",
                ["pass", "revise", "retranslate"],
                format_func=lambda x: {"pass": "通过", "revise": "修改", "retranslate": "重译"}[x],
                key=f"decision_{selected_id}_{p['paragraph_id']}",
            )
            teacher_error_types = st.multiselect(
                "问题类型（可多选）",
                ["semantic_mistranslation", "omission", "addition", "grammar",
                 "reference", "proper_name_or_term", "style", "chinese_expression",
                 "cultural_context", "footnote_or_note", "punctuation_or_format", "other"],
                format_func=lambda x: {
                    "semantic_mistranslation": "语义误译", "omission": "漏译",
                    "addition": "增译", "grammar": "语法理解", "reference": "指代关系",
                    "proper_name_or_term": "专名/术语", "style": "语体风格",
                    "chinese_expression": "中文表达", "cultural_context": "文化背景",
                    "footnote_or_note": "脚注/译者注", "punctuation_or_format": "标点/格式",
                    "other": "其他",
                }[x],
                key=f"errors_{selected_id}_{p['paragraph_id']}",
            )
            teacher_custom_error_types = ""
            if "other" in teacher_error_types:
                teacher_custom_error_types = st.text_input(
                    "其他问题归类（请填写你希望归类的审校类型）",
                    key=f"other_error_{selected_id}_{p['paragraph_id']}",
                )
            teacher_severity = st.radio(
                "严重程度",
                ["minor", "moderate", "major", "null"],
                format_func=lambda x: {
                    "minor": "轻微", "moderate": "一般", "major": "严重", "null": "无问题",
                }[x],
                key=f"severity_{selected_id}_{p['paragraph_id']}",
            )
            teacher_revision = st.text_area(
                "教师修改译文",
                value=draft_translation,
                key=f"teacher_revision_{selected_id}_{p['paragraph_id']}",
            )
            teacher_raw_comment = st.text_area(
                "教师原始说明（可选）",
                key=f"teacher_comment_{selected_id}_{p['paragraph_id']}",
            )
            if st.button(
                "提交教师审校",
                key=f"submit_l1_{selected_id}_{p['paragraph_id']}",
            ):
                record_id = storage.create_review_record(
                    None,
                    selected_id,
                    p["paragraph_id"],
                    p["source_text"],
                    draft_translation,
                )
                storage.save_l1_review(
                    None,
                    record_id,
                    teacher_decision=teacher_decision_label,
                    teacher_error_types=teacher_error_types,
                    teacher_custom_error_types=teacher_custom_error_types,
                    teacher_severity=teacher_severity,
                    teacher_revision=teacher_revision,
                    teacher_raw_comment=teacher_raw_comment,
                )
                try:
                    std = reviewer.generate_standardized_review(
                        p["source_text"],
                        draft_translation,
                        teacher_decision_label,
                        teacher_error_types,
                        teacher_severity,
                        teacher_revision,
                        teacher_raw_comment,
                    )
                    storage.save_l2_review(
                        None,
                        record_id,
                        llm_raw_normalized_response=std["raw"],
                        normalized_review=std["normalized"],
                        llm_model=std["model"],
                        prompt_version=std["prompt_version"],
                        normalized_status=std["status"],
                    )
                except Exception as e:  # noqa: BLE001
                    # L1 已保存，LLM 失败不丢失教师数据
                    st.warning(f"AI 标准化生成失败，已保留教师 L1：{e}")
                st.success("教师审校已提交。")
                st.rerun()
        else:
            # 已有审校记录，展示 L1/L2/L3 状态
            st.caption(
                f"L1：{latest_record.get('teacher_decision') or '未提交'} ｜ "
                f"L2：{latest_record.get('normalized_status') or '未生成'} ｜ "
                f"verified：{'是' if latest_record.get('verified') else '否'}"
            )
            if latest_record.get("teacher_raw_comment"):
                st.markdown(f"**教师原始说明：** {latest_record['teacher_raw_comment']}")
            if latest_record.get("teacher_custom_error_types"):
                st.markdown(
                    f"**其他问题归类：** {latest_record['teacher_custom_error_types']}"
                )
            normalized = latest_record.get("normalized_review") or {}
            if normalized:
                st.markdown("**AI 整理后的审校意见：**")
                st.write(normalized)
                if not latest_record.get("verified"):
                    # 教师确认或修改后确认 L3
                    confirm_revision = st.text_area(
                        "确认后的审校内容（可修改）",
                        value=json.dumps(normalized, ensure_ascii=False, indent=2),
                        key=f"confirm_l2_{selected_id}_{p['paragraph_id']}",
                    )
                    if normalized.get("has_conflict"):
                        st.warning(
                            "检测到结构化字段与教师说明冲突，请先解决冲突后再确认。"
                            f"冲突字段：{normalized.get('conflict_fields')}"
                        )
                    elif st.button(
                        "确认（生成 L3）",
                        key=f"confirm_l3_{selected_id}_{p['paragraph_id']}",
                    ):
                        try:
                            confirmed = json.loads(confirm_revision)
                        except Exception:  # noqa: BLE001
                            confirmed = dict(normalized)
                            confirmed["normalized_comment"] = confirm_revision
                        storage.save_l3_verified(
                            None,
                            latest_record["id"],
                            verified_review=confirmed,
                            reviewer_id="teacher",
                        )
                        st.success("已确认，进入高质量审校库。")
                        st.rerun()
            else:
                # AI 标准化失败，保留 L1，可由教师手动确认 L1 作为 L2
                if st.button("手动确认 L1 作为 L2", key=f"manual_l2_{selected_id}_{p['paragraph_id']}"):
                    storage.save_l2_review(
                        None,
                        latest_record["id"],
                        llm_raw_normalized_response="（手动）",
                        normalized_review={
                            "decision": latest_record.get("teacher_decision"),
                            "error_types": latest_record.get("teacher_error_types") or [],
                            "severity": latest_record.get("teacher_severity"),
                            "teacher_revision": latest_record.get("teacher_revision") or "",
                            "teacher_raw_comment": latest_record.get("teacher_raw_comment") or "",
                            "has_conflict": False,
                            "conflict_fields": [],
                            "conflict_explanation": "",
                        },
                        llm_model="manual",
                        prompt_version="manual",
                        normalized_status="manual",
                    )
                    st.rerun()


    # ---- 全文审校意见 ----
    st.markdown("### 保存全文审校意见")
    note_type = st.selectbox(
        "意见类型",
        ["translation_error", "terminology", "fact_check", "style", "approved", "other"],
        key=f"note_type_{selected_id}",
    )
    author = st.text_input("署名（可选）", key=f"author_{selected_id}")
    note_content = st.text_area("意见内容", key=f"note_content_{selected_id}")
    if st.button("保存全文审校意见", key=f"save_note_{selected_id}"):
        if not note_content.strip():
            st.warning("意见内容不能为空。")
        else:
            storage.save_review_note(None, selected_id, note_content, note_type=note_type, author=author)
            st.success("已保存全文审校意见。")

    # ---- 已保存意见 ----
    if details["notes"]:
        st.markdown("### 已保存的审校意见")
        para_index_map = {p["paragraph_id"]: p["paragraph_index"] for p in details["paragraphs"]}
        for n in details["notes"]:
            if n["paragraph_id"] is not None:
                # 找到对应段落号，在内容前面明确标注是哪一段的意见
                para_no = para_index_map.get(n["paragraph_id"], -1)
                location = f"第 {para_no + 1} 段" if para_no >= 0 else "未知段落"
            else:
                location = "全文"
            st.write(
                f"**[{location}][{n['note_type']}]** {n['content']} —— {n['author'] or '未署名'}"
            )

# ---------- 页面主体（脚本每次交互都会整体重跑） ----------

# st.set_page_config：设置页面标题与宽屏布局（必须放在其他 st 控件之前）
st.set_page_config(page_title="阿语审校助手 MVP", layout="wide")

# st.title：页面大标题；st.caption：标题下的灰色说明文字
st.title("阿拉伯语翻译审校助手（MVP）")
st.caption(
    "第 3.2 阶段：翻译支持双模式——默认 mock（占位译文，不联网）；"
    "配置阿里云密钥后走 api 真实翻译，缺密钥时自动回退占位译文并提示。"
    "LLM 支持双模式——默认 mock（占位，不联网）；"
    "配置 DeepSeek 密钥后走 api，依次产出直接翻译、修正结果、最终仲裁与审校报告；"
    "最终仲裁以原文为最高依据，缺密钥时自动回退占位并提示。"
)

# ---- 注入自定义样式（HTML + CSS 知识，供初学者参考）----
# 先补充背景：网页的内容结构用「HTML 标签」标记（如 <div> 表示一块区域、
# <br> 表示换行），外观（颜色、字体、对齐等）由「CSS 规则」控制。
# 比喻：HTML 是骨架，CSS 是皮肤。下面要写的就是一段 CSS。
#
# st.markdown 默认只解析 Markdown 语法；我们想塞入原生 HTML/CSS，
# 所以加参数 unsafe_allow_html=True，告诉 Streamlit「这个字符串里有 HTML，
# 请把它当作网页代码渲染，而不是当成普通文字显示」。
# 参数名里的 unsafe 是在提醒：信任字符串里的 HTML 有安全风险（注入攻击），
# 因此本文件中所有用户输入的文本，都必须先经 html.escape 消毒（见下方渲染处）。
st.markdown(
    """
    <style>
    /* 下面是一个 CSS 规则。CSS 规则 = 选择器 + 声明块。
       选择器 .ar-para 表示「页面上所有 class（类名）为 ar-para 的元素」；
       开头的小数点 . 就是「按类名选元素」的固定写法。
       大括号里的每一条都是「属性名: 值;」，用于控制这些元素的外观。 */
    .ar-para {
        direction: rtl;    /* 文字书写方向：rtl = right-to-left，从右往左排（阿语习惯） */
        unicode-bidi: embed;  /* 双向文本算法：中阿混排时按内容自身方向处理字符顺序，
                                 避免阿拉伯语里的数字/英文标点被浏览器排反 */
        text-align: right;    /* 段落整体右对齐，与从右往左的书写方向配套 */
        font-size: 1.1rem;    /* 字号：1rem = 浏览器默认字号（约 16px），1.1 倍略大一点 */
        line-height: 2;       /* 行高为字号的 2 倍：行距留宽，阿语的上下变音符不被挤压 */
        font-family: "Segoe UI", "Noto Naskh Arabic", "Traditional Arabic", sans-serif;
        /* 字体优先级列表：浏览器从左往右找，第一个「本机已安装」的字体生效；
           前两个是常见阿语文档字体（Noto Naskh Arabic 为开源阿语字体），
           最后的 sans-serif 是兜底（无衬线通用字体，任何系统都有） */
    }
    /* 译文区的样式：只改文字颜色，让译文与阿语原文有视觉区分 */
    .zh-trans {
        color: #666;  /* 文字颜色：#666 是十六进制色值，表示中等灰色 */
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# st.text_area：多行文本输入框；value 只在首次渲染生效（用户编辑后不会被重置）
source = st.text_area(
    "阿拉伯语原文（支持粘贴多段，段落间用空行分隔）",
    height=300,
    value=_load_sample(),
    key="source_text",
)

# st.button：主操作按钮，点击后本次重跑返回 True
if st.button("开始翻译与审校", type="primary"):
    if not source.strip():  # str.strip：判断是否只输入了空白字符
        st.warning("请输入阿拉伯语文本。")
    else:
        # 动态占位容器：翻译过程中实时更新“原文 + 等待/已翻译”。
        # Streamlit 的 st.empty 可以在脚本执行期间被主线程反复替换内容。
        translation_placeholders: list = []  # 每段一个空占位符
        paragraphs_list: list[str] = []  # 保存段落原文，供翻译完成时一起渲染
        review_status = st.empty()  # LLM 阶段状态占位符

        def _render_progress_placeholder(
            placeholder,
            index: int,
            paragraph: str,
            translation: str | None,
        ) -> None:
            """渲染一段的进度占位：原文立即显示，译文有结果再显示。"""
            # 原文是用户输入，必须先 html.escape 防注入；换行转 <br>
            safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
            if translation is None:
                # 还没翻译完成：先显示原文 + “等待翻译…”
                body = (
                    f"**第 {index + 1} 段（阿语）**\n\n"
                    f'<div class="ar-para">{safe_paragraph}</div>\n\n'
                    f"**第 {index + 1} 段（译文）**\n\n"
                    f"*等待翻译…*"
                )
            else:
                safe_translation = html.escape(translation).replace("\n", "<br>")
                body = (
                    f"**第 {index + 1} 段（阿语）**\n\n"
                    f'<div class="ar-para">{safe_paragraph}</div>\n\n'
                    f"**第 {index + 1} 段（译文）**\n\n"
                    f'<div class="zh-trans">{safe_translation}</div>'
                )
            placeholder.markdown(body, unsafe_allow_html=True)

        def _on_paragraphs(paragraphs: list[str]) -> None:
            """切分完成后：原文立刻显示，同时为每段创建“等待翻译”占位。"""
            paragraphs_list[:] = paragraphs
            for i, paragraph in enumerate(paragraphs):
                placeholder = st.empty()
                _render_progress_placeholder(placeholder, i, paragraph, None)
                translation_placeholders.append(placeholder)

        def _on_translation(index: int, translation: str) -> None:
            """某段翻译完成：原文旁边立即显示该段译文。"""
            _render_progress_placeholder(
                translation_placeholders[index],
                index,
                paragraphs_list[index],
                translation,
            )

        def _on_review_start() -> None:
            """全部段落翻译完成：提示开始 LLM 多轮处理。"""
            review_status.info("全部段落翻译完成，正在请求 LLM 多轮处理（直接翻译/修正/最终仲裁/审校）…")

        def _on_review_done(_review_result: dict) -> None:
            """LLM 返回后：清空临时占位符，后续用完整结果区渲染。"""
            review_status.empty()
            for placeholder in translation_placeholders:
                placeholder.empty()

        # st.spinner：处理期间显示加载提示
        with st.spinner("正在处理…"):
            try:
                # 运行渐进式流水线：并发翻译 + 每段完成即时回调；
                # 结果存进 session_state，防止下次重跑时丢失；
                # 异常时不执行赋值 → 页面保留上次成功的结果（旧结果不丢）
                st.session_state["results"] = run_pipeline_progressive(
                    source,
                    on_paragraphs=_on_paragraphs,
                    on_translation=_on_translation,
                    on_review_start=_on_review_start,
                    on_review_done=_on_review_done,
                )
            except FileNotFoundError as e:
                st.error(f"数据文件缺失：{e}")
            except translator.TranslationError as e:
                # 翻译失败（网络/业务/解析错误）：异常消息已含段落号与
                # 错误码，直接展示；页面不崩溃，保留上次成功结果
                st.error(f"翻译失败：{e}")
            except reviewer.ReviewError as e:
                # 审校失败（网络/业务/解析错误）：异常消息均为中文且不含
                # 密钥，直接展示；页面不崩溃，保留上次成功结果
                # （api 翻译结果自然保留，不因审校失败丢失）
                st.error(f"审校失败：{e}")
            except ValueError as e:
                # 环境变量配置错误（如引擎取值非法、超时非正整数）
                st.error(f"配置错误：{e}")

# ---- 结果展示说明 ----
# 这里不再提供 radio 切换：最终对比页面固定同时展示四个翻译结果，
# 便于人工逐段比较“原始 API 译文 / LLM 直接翻译 / LLM 修正 / 最终结果”。
st.caption("最终结果对比：四个结果同时展示；最终结果以原文为准，不添加原文未有的信息。")

# 有结果时渲染展示区（每次重跑都会重新渲染，保证结果不消失）
if "results" in st.session_state:
    render_results(st.session_state["results"])

# 历史记录区（页面重启后仍可读取）
render_history()
