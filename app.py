# -*- coding: utf-8 -*-
"""Streamlit 页面入口（第 3.2 阶段）。

本文件只保留页面主体与交互；流水线逻辑在 pipeline.py，
四结果/命中表/报告渲染在 streamlit_ui.py，便于维护。
"""

import html

import streamlit as st

from modules import reviewer, translator
from pipeline import run_pipeline_progressive
from streamlit_ui import load_sample, render_results

# st.set_page_config：设置页面标题与宽屏布局（必须放在其他 st 控件之前）
st.set_page_config(page_title="阿语审校助手 MVP", layout="wide")

# st.title：页面大标题；st.caption：标题下的灰色说明文字
st.title("阿拉伯语翻译审校助手（MVP）")
st.caption(
    "第 3.2 阶段：翻译支持双模式——默认 mock（占位译文，不联网）；"
    "配置阿里云密钥后走 api 真实翻译，缺密钥时自动回退占位译文并提示。"
    "LLM 支持双模式——默认 mock（占位，不联网）；"
    "配置 DeepSeek 密钥后走 api，分三次独立调用产出直接翻译、修正结果、"
    "最终仲裁与审校报告；最终仲裁以原文为最高依据，缺密钥时自动回退占位并提示。"
)

# ---- 注入自定义样式（HTML + CSS）----
# .ar-para：阿语从右向左显示；.zh-trans：译文灰字区分。
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

# 阿语文本输入框（预填示例）
source = st.text_area(
    "阿拉伯语原文（支持粘贴多段，段落间用空行分隔）",
    height=300,
    value=load_sample(),
    key="source_text",
)

# 主按钮
if st.button("开始翻译与审校", type="primary"):
    if not source.strip():
        st.warning("请输入阿拉伯语文本。")
    else:
        # 动态占位：原文立即显示，译文逐个出现；LLM 阶段显示多轮处理提示
        translation_placeholders: list = []
        paragraphs_list: list[str] = []
        review_status = st.empty()

        def _render_progress_placeholder(placeholder, index: int, paragraph: str, translation: str | None) -> None:
            safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
            if translation is None:
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
            paragraphs_list[:] = paragraphs
            for i, paragraph in enumerate(paragraphs):
                placeholder = st.empty()
                _render_progress_placeholder(placeholder, i, paragraph, None)
                translation_placeholders.append(placeholder)

        def _on_translation(index: int, translation: str) -> None:
            _render_progress_placeholder(
                translation_placeholders[index],
                index,
                paragraphs_list[index],
                translation,
            )

        def _on_review_start() -> None:
            review_status.info("全部段落翻译完成，正在请求 LLM 多轮处理（直接翻译/修正/最终仲裁/审校）…")

        def _on_review_done(_review_result: dict) -> None:
            review_status.empty()
            for placeholder in translation_placeholders:
                placeholder.empty()

        with st.spinner("正在处理…"):
            try:
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
                st.error(f"翻译失败：{e}")
            except reviewer.ReviewError as e:
                st.error(f"审校失败：{e}")
            except ValueError as e:
                st.error(f"配置错误：{e}")

# 结果展示说明
st.caption("最终结果对比：四个结果同时展示；最终结果以原文为准，不添加原文未有的信息。")

# 有结果时渲染展示区
if "results" in st.session_state:
    render_results(st.session_state["results"])
