# -*- coding: utf-8 -*-
"""Streamlit 展示层：结果渲染、命中表格式化、示例文本读取。

作用：把 app.py 中与 UI 渲染相关的代码拆出，降低 app.py 体积。
     本模块只包含 st.* 展示逻辑，不包含流水线编排。
"""

import html
from pathlib import Path

import streamlit as st

from modules import settings

BASE_DIR = Path(__file__).parent

# 处理方式中文标签
_CHECK_LABELS = {
    "force_check": "强检查",
    "suggest": "推荐检查",
    "context_warning": "语境检查",
}

TERM_COLUMNS = ["阿语原文", "中文译文", "类别", "领域", "备注", "处理方式", "段落", "出现次数"]
NAME_COLUMNS = ["阿语原文", "中文译文", "类别", "备注", "段落", "出现次数"]


def load_sample() -> str:
    """读取示例文本，供输入框首次渲染预填。"""
    sample_path = BASE_DIR / "data" / "samples" / "politics_001.txt"
    if sample_path.exists():
        return sample_path.read_text(encoding="utf-8")
    return ""


def _hits_to_rows(hits: list[dict], columns: list[str]) -> list[dict]:
    """把扫描命中列表转成展示行。"""
    rows = []
    for hit in hits:
        check_value = hit.get("处理方式", "")
        check_label = _CHECK_LABELS.get(check_value, check_value)
        row = {
            "阿语原文": hit.get("阿语原文", ""),
            "中文译文": hit.get("中文译文", ""),
            "类别": hit.get("类别", ""),
            "领域": hit.get("领域", ""),
            "备注": hit.get("备注", ""),
            "处理方式": check_label,
            "段落": ", ".join(str(p) for p in hit.get("paragraphs", [])),
            "出现次数": hit.get("count", ""),
        }
        rows.append({col: row[col] for col in columns})
    return rows


def render_results(results: dict) -> None:
    """渲染结果展示区（四结果对比 / 术语命中 / 专名命中 / 审校报告）。"""
    if results.get("translation_fallback"):
        st.warning("已配置 API 模式但未配置密钥，本次使用占位译文。")
    if results.get("review_fallback"):
        st.warning("已配置 LLM 校准 API 模式但未配置密钥，本次使用占位 LLM 结果与占位报告。")

    st.subheader("翻译结果对比（四个结果）")
    st.caption("最终结果以原文为最高依据进行仲裁，不添加原文未有的信息。")

    translations = results.get("translations", [])
    direct_translations = results.get("direct_translations", translations)
    corrected_translations = results.get("corrected_translations", translations)
    final_translations = results.get("final_translations", corrected_translations)

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
        safe_paragraph = html.escape(paragraph).replace("\n", "<br>")
        st.markdown(f'<div class="ar-para">{safe_paragraph}</div>', unsafe_allow_html=True)

        def _render_one(label: str, text: str, source_is_api: bool, degraded_to: str | None = None) -> None:
            if source_is_api:
                if results.get("translation_mode") == settings.API_ENGINE and not results.get(
                    "translation_fallback"
                ):
                    suffix = ""
                else:
                    suffix = "·占位"
                st.markdown(f"**第 {i} 段（{label}{suffix}）**")
            else:
                if results.get("review_mode") != settings.API_ENGINE or results.get(
                    "review_fallback"
                ):
                    suffix = "·占位"
                elif degraded_to:
                    suffix = f"·已降级到{degraded_to}"
                else:
                    suffix = ""
                st.markdown(f"**第 {i} 段（{label}{suffix}）**")
            st.markdown(
                f'<div class="zh-trans">{html.escape(text)}</div>',
                unsafe_allow_html=True,
            )

        _render_one("原始 API 译文", raw_translation, True)
        _render_one(
            "LLM 直接翻译结果",
            direct_translation,
            False,
            "API译文" if results.get("direct_degraded") else None,
        )
        _render_one(
            "LLM 修正结果",
            corrected_translation,
            False,
            "API译文" if results.get("corrected_degraded") else None,
        )
        _render_one(
            "LLM 最终结果（以原文为准）",
            final_translation,
            False,
            "修正结果" if results.get("final_degraded") else None,
        )
        st.divider()

    tradeoff_notes = results.get("tradeoff_notes", "")
    if tradeoff_notes:
        st.subheader("翻译取舍说明")
        if results.get("tradeoff_degraded"):
            st.warning("本次未生成完整取舍说明（已降级），以下为缺省提示。")
        st.markdown(tradeoff_notes)

    st.subheader("术语命中")
    term_rows = _hits_to_rows(results["term_hits"], TERM_COLUMNS)
    if term_rows:
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
    if results.get("review_mode") == settings.API_ENGINE and not results.get(
        "review_fallback"
    ):
        st.markdown(results["report"])
        st.caption("以上审校报告由 LLM 生成，仅供人工复核参考；请以原文与术语库为准。")
    else:
        st.info(results["report"])
