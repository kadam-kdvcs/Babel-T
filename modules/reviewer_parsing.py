# -*- coding: utf-8 -*-
"""LLM 结果解析模块（阶段 3.2）。

作用：从 reviewer.py 拆出的纯解析逻辑，负责把 LLM 返回的 markdown 文本
拆成译文列表、降级标志、取舍说明与审校报告。这里不发起网络请求。
"""

import re  # 标准库：解析「第N段：…」文本


# LLM 返回内容的结构标记
_DIRECT_SECTION_MARKER = "## 直接翻译结果"
_CORRECTED_SECTION_MARKER = "## API译文修正结果"
_FINAL_SECTION_MARKER = "## 最终结果"
_TRADEOFF_SECTION_MARKER = "## 翻译取舍说明"
_REPORT_SECTION_MARKER = "## 审校报告"


def _parse_numbered_translations(
    text: str, paragraphs: list[str], translations: list[str]
) -> list[str]:
    """从「第N段：…」文本中解析出译文列表。

    作用：LLM 输出的每一轮结果通常按「第1段：...」逐行列出。这里用正则
          提取每个「第N段：」到下一个段号/标题之间的内容；若提取到的
          条数与段落数不一致，则说明模型格式不规范，回退使用兜底译文。
    输入：text —— 某段结果内的 markdown 文本；
          paragraphs —— 阿语段落列表（用于校验数量）；
          translations —— 兜底译文列表（数量不符时使用）。
    输出：list[str] —— 与 paragraphs 等长的译文列表。
    """
    return _parse_numbered_translations_with_status(text, paragraphs, translations)[0]


def _parse_numbered_translations_with_status(
    text: str, paragraphs: list[str], fallback: list[str]
) -> tuple[list[str], bool]:
    """解析某轮译文，并返回“是否发生了降级回退”。

    作用：与 _parse_numbered_translations 相同，但它额外返回一个布尔值：
          False 表示 LLM 输出解析成功；True 表示解析失败，页面应明确标注
          “已降级到兜底译文”，避免把 API 译文伪装成 LLM 成功结果。
    输入：text —— 某段结果内的 markdown 文本；
          paragraphs —— 阿语段落列表（用于校验数量）；
          fallback —— 兜底译文列表（解析失败时使用）。
    输出：tuple[list[str], bool] —— (译文列表, 是否降级)。
    """
    pattern = re.compile(
        r"第(\d+)段[:：](.*?)(?=\n\s*第\d+段[:：]|\n\s*## |\Z)", re.S
    )
    matches = pattern.findall(text)
    if len(matches) != len(paragraphs):
        return list(fallback), True

    result = []
    for number_str, segment in matches:
        clean = re.sub(r"[*_#>`]", "", segment).strip()
        result.append(clean)
    return result, False


def _parse_review_bundle(
    content: str, paragraphs: list[str], corrected_translations: list[str]
) -> dict:
    """把“最终仲裁”回复拆成 最终结果 / 翻译取舍说明 / 审校报告。

    作用：解析最终仲裁那一次 LLM 调用。返回最终译文列表、取舍说明、
          审校报告，以及 final_degraded / tradeoff_degraded 标志，
          供页面明确标注“已降级处理”。
    """
    def _section_text(start_marker: str, end_marker: str) -> str | None:
        """取 start_marker 到 end_marker 之间的文本；缺少标记返回 None。"""
        if start_marker not in content or (end_marker and end_marker not in content):
            return None
        part = content.split(start_marker, 1)[1]
        if end_marker:
            part = part.split(end_marker, 1)[0]
        return part

    has_any_section = any(
        marker in content
        for marker in (_FINAL_SECTION_MARKER, _TRADEOFF_SECTION_MARKER, _REPORT_SECTION_MARKER)
    )
    if not has_any_section:
        return {
            "report": content.strip(),
            "final_translations": list(corrected_translations),
            "final_degraded": True,
            "tradeoff_notes": "（缺省）模型未输出翻译取舍说明，请人工复核直接翻译与修正结果。",
            "tradeoff_degraded": True,
        }

    final_text = _section_text(_FINAL_SECTION_MARKER, _TRADEOFF_SECTION_MARKER)
    if final_text is None:
        final_text = _section_text(_FINAL_SECTION_MARKER, _REPORT_SECTION_MARKER)
    if final_text is not None:
        final_translations, final_degraded = _parse_numbered_translations_with_status(
            final_text, paragraphs, corrected_translations
        )
    else:
        final_translations = list(corrected_translations)
        final_degraded = True

    tradeoff_text = _section_text(_TRADEOFF_SECTION_MARKER, _REPORT_SECTION_MARKER)
    if tradeoff_text is not None and tradeoff_text.strip():
        tradeoff_notes = tradeoff_text.strip()
        tradeoff_degraded = False
    else:
        tradeoff_notes = "（缺省）模型未输出翻译取舍说明，请人工复核直接翻译与修正结果。"
        tradeoff_degraded = True

    if _REPORT_SECTION_MARKER in content:
        report = content.split(_REPORT_SECTION_MARKER, 1)[1].strip()
    else:
        report = content.strip()

    return {
        "report": report,
        "final_translations": final_translations,
        "final_degraded": final_degraded,
        "tradeoff_notes": tradeoff_notes,
        "tradeoff_degraded": tradeoff_degraded,
    }
