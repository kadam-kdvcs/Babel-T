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
from modules import glossary, reviewer, segmenter, settings, translator

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
) -> dict:
    """执行完整处理流水线，并支持“逐段完成即回调”。

    作用：与 run_pipeline 一样返回完整结果，但额外暴露四个回调：
          - on_paragraphs(paragraphs)：切分完成后调用（UI 可先创建占位符）；
          - on_translation(index, translation)：某段 API 翻译完成立即调用
            （0 基 index，主线程安全，可实时更新 st.empty）；
          - on_review_start()：全部翻译完成后、开始 LLM 纠正/审校前调用；
          - on_review_done(review_result)：LLM 返回后调用。
          API 机器翻译在 translate_paragraphs_parallel 中并发执行；
          LLM 纠正以整篇文章为输入，必须等全部段落翻译完成后才能开始。
    输入：text —— 用户输入的整篇阿语文本；回调均可选。
    输出：dict —— 键：
          paragraphs           阿语段落列表
          translations         原始 API 译文列表（占位或真实翻译）
          direct_translations  LLM 直接翻译原文的结果列表
          corrected_translations LLM 基于 API 译文修正后的结果列表
          final_translations   LLM 结合原文/直接/修正后的最终仲裁结果列表
          tradeoff_notes       LLM 对直接翻译与修正结果的取舍说明
          term_hits            术语命中列表
          name_hits            专名命中列表
          report               审校报告字符串（占位或 LLM 生成）
          translation_mode     "mock" 或 "api"（本次生效的翻译模式）
          translation_fallback True 表示「api 模式但缺密钥，本次用了占位译文」
          review_mode          "mock" 或 "api"（本次生效的审校模式）
          review_fallback      True 表示「api 模式但缺密钥，本次用了占位报告」
    异常：数据文件缺失时抛出 FileNotFoundError；翻译失败抛
          translator.TranslationError 家族；审校失败抛 reviewer.ReviewError
          家族；环境变量非法抛 ValueError（全部由 UI 层转成页面提示）。
    """
    # 段落切分：整篇文本 → 段落列表
    paragraphs = segmenter.segment_paragraphs(text)
    if on_paragraphs is not None:
        on_paragraphs(paragraphs)

    # 加载术语库（六列 CSV）与专名库（四列 CSV），路径相对项目根目录
    terms = glossary.load_glossary(BASE_DIR / "data" / "terms.csv")
    names = glossary.load_glossary(BASE_DIR / "data" / "proper_names.csv")

    # 在段落中扫描命中（两库共用同一个扫描函数）
    term_hits = glossary.scan_glossary(paragraphs, terms)
    name_hits = glossary.scan_glossary(paragraphs, names)

    # 翻译：mock 模式生成占位译文，api 模式并发调用阿里云真实翻译；
    # on_translation 让 Streamlit 在主线程实时更新已完成的段落。
    translations = translator.translate_paragraphs_parallel(
        paragraphs,
        term_hits,
        name_hits,
        on_translation=on_translation,
    )

    # 读取本次生效的翻译配置，确定「模式」与「是否回退占位」两个展示键。
    # 页面直接读这两个键渲染提示，不在 UI 层重复推断配置逻辑；
    # 环境变量非法时这里（以及 translate_paragraphs_parallel 内部）会抛 ValueError。
    config = settings.load_translation_config()
    translation_mode = config.engine
    translation_fallback = (
        config.engine == settings.API_ENGINE and not config.has_credentials
    )

    # LLM 步骤开始前通知 UI（可更新“正在 LLM 纠正/审校”状态）
    if on_review_start is not None:
        on_review_start()

    # 审校结果包（阶段 3.2：三次独立调用拿到「直接翻译 / 修正结果 / 最终结果 /
    # 取舍说明 / 审校报告」；mock 或缺密钥时均为占位数据，由页面黄色提示告知用户）
    review_result = reviewer.generate_review_bundle(
        paragraphs, translations, term_hits, name_hits
    )
    if on_review_done is not None:
        on_review_done(review_result)

    report = review_result["report"]
    direct_translations = review_result["direct_translations"]
    corrected_translations = review_result["corrected_translations"]
    final_translations = review_result["final_translations"]
    tradeoff_notes = review_result.get("tradeoff_notes", "")

    # 读取本次生效的审校配置，确定「模式」与「是否回退占位」两个展示键
    # （与上方翻译配置块对称：页面直接读这两个键渲染提示，不在 UI 层
    # 重复推断配置逻辑；环境变量非法时这里会抛 ValueError）
    review_config = settings.load_review_config()
    review_mode = review_config.engine
    review_fallback = (
        review_config.engine == settings.API_ENGINE and not review_config.has_credentials
    )

    return {
        "paragraphs": paragraphs,
        "translations": translations,
        "direct_translations": direct_translations,
        "corrected_translations": corrected_translations,
        "final_translations": final_translations,
        "tradeoff_notes": tradeoff_notes,
        "term_hits": term_hits,
        "name_hits": name_hits,
        "report": report,
        "translation_mode": translation_mode,
        "translation_fallback": translation_fallback,
        "review_mode": review_mode,
        "review_fallback": review_fallback,
    }


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
