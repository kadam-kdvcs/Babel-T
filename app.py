"""Streamlit 页面入口（第 1 阶段）：阿拉伯语翻译审校助手 MVP。

页面布局：
1. 标题与说明
2. 阿语文本输入框（预填示例文本）
3. 「开始翻译与审校」按钮
4. 五个展示区：双语对照 / 术语命中 / 专名命中 / 审校报告

本文件是唯一包含 UI 的模块；流水线逻辑集中在 run_pipeline()，
不依赖页面状态，可在命令行直接调用验证。
"""

import html  # 标准库：转义用户文本，防止 HTML 注入
from pathlib import Path  # 标准库：跨平台路径处理

import streamlit as st  # 页面框架：所有界面控件都来自这里

# 业务模块：段落切分 / 词库扫描 / 占位翻译 / 占位报告
from modules import glossary, reviewer, segmenter, translator

# 项目根目录：本文件所在目录（无论从哪个目录启动 streamlit 都有效）
BASE_DIR = Path(__file__).parent

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
    """执行完整处理流水线（纯本地，无任何 API 调用）。

    作用：把输入的阿语文本依次送过「切分 → 加载词库 → 扫描 → 占位译文 → 占位报告」，
          返回结果字典供页面渲染；也支持命令行直接调用验证。
    输入：text —— 用户输入的整篇阿语文本。
    输出：dict —— 键：
          paragraphs   阿语段落列表
          translations 占位译文列表
          term_hits    术语命中列表
          name_hits    专名命中列表
          report       占位审校报告字符串
    异常：数据文件缺失时抛出 FileNotFoundError（由 UI 层转成提示）。
    """
    # 段落切分：整篇文本 → 段落列表
    paragraphs = segmenter.segment_paragraphs(text)

    # 加载术语库（六列 CSV）与专名库（四列 CSV），路径相对项目根目录
    terms = glossary.load_glossary(BASE_DIR / "data" / "terms.csv")
    names = glossary.load_glossary(BASE_DIR / "data" / "proper_names.csv")

    # 在段落中扫描命中（两库共用同一个扫描函数）
    term_hits = glossary.scan_glossary(paragraphs, terms)
    name_hits = glossary.scan_glossary(paragraphs, names)

    # 占位译文与占位审校报告（后续阶段分别替换为 API 与 LLM 实现）
    translations = translator.translate_paragraphs(paragraphs)
    report = reviewer.generate_review_report(paragraphs, translations, term_hits, name_hits)

    return {
        "paragraphs": paragraphs,
        "translations": translations,
        "term_hits": term_hits,
        "name_hits": name_hits,
        "report": report,
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
    """渲染五个结果展示区。

    作用：把 run_pipeline 的结果按「双语对照 / 术语命中 / 专名命中 / 审校报告」渲染到页面。
    输入：results —— run_pipeline 的输出 dict。
    输出：无（直接向页面输出控件）。
    """
    st.subheader("双语对照")
    # zip：把段落与译文一一配对；enumerate：从 1 开始编号段落
    for i, (paragraph, translation) in enumerate(
        zip(results["paragraphs"], results["translations"]), start=1
    ):
        st.markdown(f"**第 {i} 段（阿语）**")
        # html.escape：转义用户文本中的 < > & 等字符，防止破坏页面（注入防护）
        # str.replace：段内换行 \n 转成 <br>，markdown 单换行不渲染，需要手动转
        safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
        st.markdown(f'<div class="ar-para">{safe_paragraph}</div>', unsafe_allow_html=True)
        st.markdown(f"**第 {i} 段（译文·占位）**")
        st.markdown(f'<div class="zh-trans">{html.escape(translation)}</div>', unsafe_allow_html=True)
        st.divider()

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
    st.info(results["report"])


# ---------- 页面主体（脚本每次交互都会整体重跑） ----------

# st.set_page_config：设置页面标题与宽屏布局（必须放在其他 st 控件之前）
st.set_page_config(page_title="阿语审校助手 MVP", layout="wide")

# st.title：页面大标题；st.caption：标题下的灰色说明文字
st.title("阿拉伯语翻译审校助手（MVP）")
st.caption("第 1 阶段：纯本地流水线。译文与审校报告为占位内容，后续阶段接入翻译 API 与 LLM。")

# 注入 RTL 样式：只作用于标有 ar-para 类的阿语容器，不影响页面其他部分
# （direction: rtl 让阿语从右往左排；unicode-bidi: embed 处理中阿混排的字符顺序）
st.markdown(
    """
    <style>
    .ar-para {
        direction: rtl;
        unicode-bidi: embed;
        text-align: right;
        font-size: 1.1rem;
        line-height: 2;
        font-family: "Segoe UI", "Noto Naskh Arabic", "Traditional Arabic", sans-serif;
    }
    .zh-trans {
        color: #666;
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
        # st.spinner：处理期间显示加载提示
        with st.spinner("正在处理…"):
            try:
                # 运行流水线，结果存进 session_state，防止下次重跑时丢失
                st.session_state["results"] = run_pipeline(source)
            except FileNotFoundError as e:
                st.error(f"数据文件缺失：{e}")

# 有结果时渲染展示区（每次重跑都会重新渲染，保证结果不消失）
if "results" in st.session_state:
    render_results(st.session_state["results"])
