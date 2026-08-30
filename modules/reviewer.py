# -*- coding: utf-8 -*-
"""审校报告模块（阶段 3：可配置双模式）。

模块作用
--------
生成审校报告文本，支持两种模式（由环境变量 REVIEW_ENGINE 决定，
见 load_review_config）：

- mock 模式（默认）：不联网，生成与阶段 1 逐字节一致的占位报告，
  用于本地演示与测试；
- api 模式：读取 prompts/review_report_prompt.md 提示词模板，把
  段落/译文/术语与专名命中填入用户消息，调用 DeepSeek（或任意
  OpenAI 兼容接口）生成 8 项结构的 markdown 审校报告。
  翻译 API 无法接入术语表（阶段 2 已知约束），术语一致性必须由
  LLM 在审校报告中把关，因此 LLM 报告以真实翻译结果为审校依据。

本模块是纯函数模块（无 UI、无全局可变状态）。配置的定义与加载
（环境变量名、默认值、ReviewConfig、load_review_config）集中在
modules/settings.py——本模块只通过参数接收配置，不读取环境变量，
保证可测试性。模块内不 import python-dotenv：.env 的加载只发生在
app.py 顶部，命令行直接调用 run_pipeline 时需要自行 load_dotenv。

失败策略（与翻译模块对称）
--------------------------
- api 模式缺密钥（has_credentials 为 False）→ 不报错，回退占位报告，
  由页面显示黄色提示；
- 调用失败（超时/连接失败/HTTP 非 200/响应解析失败）→ 抛本模块的
  ReviewError 家族异常，由 app.py 转 st.error，旧结果保留。

安全约定
--------
- 绝不把 API 密钥写入代码、日志、错误消息；
- 异常消息全部为中文，业务错误只带响应文本前 200 字符；
- 密钥只允许存在于环境变量（.env）中，.env 已被 .gitignore 忽略。
"""

import re  # 标准库：解析 LLM 输出中「纠正后译文」的段号行
from pathlib import Path  # 标准库：跨平台路径处理（定位提示词模板）

import requests  # 第三方库：HTTP 客户端，用于调用 LLM 审校接口

# 配置集中管理（阶段 3）：环境变量名/默认值/ReviewConfig/
# load_review_config 全部定义在 modules/settings.py，本模块只按需导入
from modules.settings import (
    MOCK_ENGINE,             # 引擎标识：mock（占位，默认）
    ReviewConfig,            # 配置对象（参数类型标注用）
    load_review_config,      # 加载配置（审校入口处调用）
)


# ---------------------------------------------------------------------------
# 模块级私有常量
# ---------------------------------------------------------------------------

# 提示词模板目录与三阶段模板路径：
# - direct_translation_prompt.md：直接翻译原文（不提供 API 译文）
# - correct_translation_prompt.md：基于“原文 + API 译文”修正
# - review_report_prompt.md：最终仲裁 + 取舍说明 + 审校报告（不提供 API 译文）
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_DIRECT_PROMPT_PATH = _PROMPT_DIR / "direct_translation_prompt.md"
_CORRECT_PROMPT_PATH = _PROMPT_DIR / "correct_translation_prompt.md"
_PROMPT_PATH = _PROMPT_DIR / "review_report_prompt.md"  # 保留旧兼容名，实际指最终仲裁模板
_STANDARDIZED_PROMPT_PATH = _PROMPT_DIR / "standardized_review_prompt.md"

# 各阶段模板的占位符（写进模板时用花括号包住，如 {source_paragraphs}）
_DIRECT_PLACEHOLDERS = ("source_paragraphs", "term_hits", "name_hits")
_CORRECT_PLACEHOLDERS = ("source_paragraphs", "translations", "term_hits", "name_hits")
_PLACEHOLDERS = ("source_paragraphs", "direct_translations", "corrected_translations",
                 "term_hits", "name_hits")
_STANDARDIZED_PLACEHOLDERS = (
    "source_text", "draft_translation", "teacher_decision",
    "teacher_revision", "teacher_raw_comment",
)

# 模板分隔标记：程序按用户标记拆出用户消息段（占位符所在），
# 按系统标记截取系统消息段（去掉文件头部的维护者说明）。
# 校验要求两个标记都存在、且系统标记在用户标记之前。
_USER_SECTION_MARKER = "## 用户消息"
_SYSTEM_SECTION_MARKER = "## 系统消息"

# LLM 返回内容的结构标记：
# - 直接翻译响应：`## 直接翻译结果`
# - 修正响应：`## API译文修正结果`
# - 最终仲裁响应：`## 最终结果` + `## 翻译取舍说明` + `## 审校报告`
# 若模型没有按这些标题输出，程序会安全回退。
_DIRECT_SECTION_MARKER = "## 直接翻译结果"
_CORRECTED_SECTION_MARKER = "## API译文修正结果"
_FINAL_SECTION_MARKER = "## 最终结果"
_TRADEOFF_SECTION_MARKER = "## 翻译取舍说明"
_REPORT_SECTION_MARKER = "## 审校报告"

# LLM 采样温度：固定 0.3（偏低温，审校任务希望输出稳定、少发散；
# 阶段 3 决策：不配环境变量）
_TEMPERATURE = 0.3


# ---------------------------------------------------------------------------
# 异常体系（全部中文消息，绝不包含任何密钥）
# ---------------------------------------------------------------------------

class ReviewError(Exception):
    """审校异常基类（阶段 3 起，一切审校失败都抛此体系下的异常）。

    作用：让调用方（app.py）用一条 except 捕获全部审校失败，
          统一转成页面错误提示，而不是让页面整体崩溃。
    输入：message —— 中文错误消息（不含密钥）。
    输出：异常实例。
    """


class ReviewNetworkError(ReviewError):
    """网络层失败：超时 / 连接失败。

    作用：区分「网络没通」与「接口返回了业务错误」两类失败，
          方便排查是网络问题还是服务问题。
    输入：message —— 中文错误消息。
    输出：异常实例。
    """


class ReviewBusinessError(ReviewError):
    """业务层失败：HTTP 状态码非 200。

    作用：携带状态码与响应文本片段（前 200 字符），便于排查
          是凭证问题还是服务问题。
    输入：message —— 中文错误消息；status_code —— HTTP 状态码；
          snippet —— 响应文本前 200 字符的截断片段。
    输出：异常实例，携带 status_code / snippet 属性。
    """

    def __init__(self, message: str, status_code: int | None = None,
                 snippet: str | None = None):
        self.status_code = status_code
        self.snippet = snippet
        super().__init__(message)


class ReviewParseError(ReviewError):
    """解析层失败：响应不是合法 JSON，或缺少 choices / message.content。

    作用：区分「响应格式不对」与「接口明确报错」，前者通常是
          接口协议变动，需要开发排查。
    输入：message —— 中文错误消息。
    输出：异常实例。
    """


# ---------------------------------------------------------------------------
# 审校入口（双模式编排）
# ---------------------------------------------------------------------------

def generate_review_report(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict],
    name_hits: list[dict],
) -> str:
    """生成审校报告文本（markdown 格式，兼容入口）。

    作用：只返回审校报告字符串。实现上委托给 generate_review_bundle，
          从同一份结果里取 "report" 键——这样旧调用方（含旧测试）无需
          改动，同时新页面可以直接使用 generate_review_bundle 拿纠正译文。
    输入/输出/异常：与 generate_review_bundle 相同，仅输出为 str 报告。
    """
    return generate_review_bundle(paragraphs, translations, term_hits, name_hits)["report"]


def generate_direct_translations(
    paragraphs: list[str],
    term_hits: list[dict] | None = None,
    name_hits: list[dict] | None = None,
    fallback_translations: list[str] | None = None,
) -> dict:
    """LLM 直接翻译原文（独立公开入口，供阶段 4 逐步保存）。

    输入：paragraphs —— 阿语段落；term_hits / name_hits —— 命中列表。
    输出：dict：
          - translations: list[str]
          - degraded: bool（True 表示解析失败回退到 API 译文）
    """
    if not paragraphs:
        return {"translations": [], "degraded": False}
    config = load_review_config()
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return {
            "translations": [
                f"（占位直接翻译·第{i + 1}段）待接入 LLM 直接翻译"
                for i in range(len(paragraphs))
            ],
            "degraded": False,
        }

    hits_text = _format_hits_text(term_hits, name_hits)
    source_text = _format_numbered(paragraphs, "段")
    content = _call_llm(
        config,
        _DIRECT_PROMPT_PATH,
        _DIRECT_PLACEHOLDERS,
        {
            "source_paragraphs": source_text,
            "term_hits": hits_text,
            "name_hits": hits_text,
        },
    )
    translations, degraded = _parse_numbered_translations_with_status(
        content, paragraphs, fallback_translations or []
    )
    # 若解析失败，兜底应使用 API 译文；这里由调用方传入，下面不能直接得到 API，
    # 因此这里用空列表的兜底只在无 API 译文时有意义。实际 bundle 流程中，
    # pipeline 会在回调前把 API 译文传进来，本公开函数保持简单抛回原始退化。
    if degraded and not translations:
        # 没有兜底可回退时保留占位（正常情况下 bundle 会覆盖）
        translations = [f"（降级·第{i + 1}段）待人工复核" for i in range(len(paragraphs))]
    return {"translations": translations, "degraded": degraded}


def generate_corrected_translations(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict] | None = None,
    name_hits: list[dict] | None = None,
) -> dict:
    """LLM 基于“原文 + API 译文”修正（独立公开入口）。

    输出：dict —— translations / degraded。
    """
    if not paragraphs:
        return {"translations": [], "degraded": False}
    config = load_review_config()
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return {
            "translations": [
                f"（占位修正译文·第{i + 1}段）待接入 LLM 修正"
                for i in range(len(paragraphs))
            ],
            "degraded": False,
        }

    hits_text = _format_hits_text(term_hits, name_hits)
    source_text = _format_numbered(paragraphs, "段")
    content = _call_llm(
        config,
        _CORRECT_PROMPT_PATH,
        _CORRECT_PLACEHOLDERS,
        {
            "source_paragraphs": source_text,
            "translations": _format_numbered(translations, "段译文"),
            "term_hits": hits_text,
            "name_hits": hits_text,
        },
    )
    corrected, degraded = _parse_numbered_translations_with_status(
        content, paragraphs, translations
    )
    return {"translations": corrected, "degraded": degraded}


def generate_final_arbitration(
    paragraphs: list[str],
    direct_translations: list[str],
    corrected_translations: list[str],
    term_hits: list[dict] | None = None,
    name_hits: list[dict] | None = None,
) -> dict:
    """LLM 最终仲裁 + 取舍说明 + 审校报告（独立公开入口）。

    输出：dict —— final_translations / tradeoff_notes / report /
          final_degraded / tradeoff_degraded。
    """
    if not paragraphs:
        return {
            "final_translations": [],
            "tradeoff_notes": "",
            "report": "",
            "final_degraded": False,
            "tradeoff_degraded": False,
        }
    config = load_review_config()
    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return {
            "final_translations": [
                f"（占位最终结果·第{i + 1}段）待接入 LLM 仲裁"
                for i in range(len(paragraphs))
            ],
            "tradeoff_notes": "（占位）翻译取舍说明：待接入 LLM 最终仲裁后生成。",
            "report": _generate_mock_report(paragraphs, term_hits or [], name_hits or []),
            "final_degraded": False,
            "tradeoff_degraded": False,
        }

    hits_text = _format_hits_text(term_hits, name_hits)
    source_text = _format_numbered(paragraphs, "段")
    content = _call_llm(
        config,
        _PROMPT_PATH,
        _PLACEHOLDERS,
        {
            "source_paragraphs": source_text,
            "direct_translations": _format_numbered(direct_translations, "段直接翻译"),
            "corrected_translations": _format_numbered(corrected_translations, "段修正译文"),
            "term_hits": hits_text,
            "name_hits": hits_text,
        },
    )
    return _parse_review_bundle(content, paragraphs, corrected_translations)



def generate_standardized_review(
    source_text: str,
    draft_translation: str,
    teacher_decision: str,
    teacher_error_types: list[str] | None = None,
    teacher_severity: str = "null",
    teacher_revision: str = "",
    teacher_raw_comment: str = "",
) -> dict:
    """生成 L2 AI 标准化审校。

    作用：把教师原始审校（结论/最终译文/原始说明）交给 LLM，输出标准化
    审校 JSON。教师不再需要手工填写“问题类型/严重程度”，由系统自动推断；
    推断结果保存在 normalized_review（L2）中，与教师原始输入分开。

    返回 dict：
      - raw: LLM 原始响应
      - normalized: 解析后的 L2 结构化 dict（含推断的 error_types/severity）
      - model / prompt_version / status("success"|"failed")
    若 LLM 返回不是合法 JSON，status="failed" 且 raw 仍保留，L1 不受影响。

    兼容说明：teacher_error_types / teacher_severity 参数保留仅供旧调用方
    使用；新调用只需传 source / draft / decision / revision / raw_comment。
    """
    config = load_review_config()
    # 基础推断：即使 mock、缺 key 或 LLM 解析失败，也能给出一份可读的 L2
    inferred = _infer_standardized_review(
        source_text,
        draft_translation,
        teacher_decision,
        teacher_revision or "",
        teacher_raw_comment or "",
    )

    if config.engine == MOCK_ENGINE or not config.has_credentials:
        return {
            "raw": "（mock）标准化审校未调用真实 LLM。",
            "normalized": inferred,
            "model": config.model,
            "prompt_version": "v1",
            "status": "success",
        }

    replacements = {
        "source_text": source_text,
        "draft_translation": draft_translation,
        "teacher_decision": teacher_decision,
        "teacher_revision": teacher_revision or "",
        "teacher_raw_comment": teacher_raw_comment or "",
    }
    raw = _call_llm(
        config,
        _STANDARDIZED_PROMPT_PATH,
        _STANDARDIZED_PLACEHOLDERS,
        replacements,
    )
    normalized, ok = _parse_standardized_review_json(raw, inferred)
    return {
        "raw": raw,
        "normalized": normalized,
        "model": config.model,
        "prompt_version": "v1",
        "status": "success" if ok else "failed",
    }


def _infer_standardized_review(
    source_text: str,
    draft_translation: str,
    teacher_decision: str,
    teacher_revision: str,
    teacher_raw_comment: str,
) -> dict:
    """根据教师原始输入推断 L2 结构化字段（mock/解析失败时用）。

    输入：原文、AI 原始译文、教师结论、教师最终译文、教师原始说明。
    输出：标准 normalized_review dict。
    注意：推断结果与 teacher_raw_comment 分开保存，绝不改写原始说明。
    """
    draft_text = draft_translation or ""
    comment = teacher_raw_comment or ""
    revision = teacher_revision or ""
    has_change = bool(draft_text.strip()) and bool(revision.strip()) and revision.strip() != draft_text.strip()
    combined = f"{comment}\n{revision}".strip()

    error_types: list[str] = []
    if any(key in combined for key in ("漏", "缺", "省略")):
        error_types.append("omission")
    if any(key in combined for key in ("错", "误", "语义", "理解")):
        error_types.append("semantic_mistranslation")
    if any(key in combined for key in ("不顺", "中文", "表达", "措辞", "语序")):
        error_types.append("chinese_expression")
    if any(key in combined for key in ("术语", "专名", "固定译法", "译名")):
        error_types.append("proper_name_or_term")
    if any(key in combined for key in ("风格", "语体", "语气")):
        error_types.append("style")
    if any(key in combined for key in ("文化", "背景")):
        error_types.append("cultural_context")
    if not error_types and (has_change or comment):
        error_types = ["semantic_mistranslation"] if teacher_decision == "retranslate" else ["chinese_expression"]

    if any(key in combined for key in ("完全", "彻底", "严重", "重译", "通篇")):
        severity = "major"
    elif any(key in combined for key in ("漏", "错", "误", "不顺", "问题")):
        severity = "moderate"
    elif has_change or comment:
        severity = "minor"
    else:
        severity = "null"

    if comment:
        problem_summary = comment.strip()
    elif has_change:
        problem_summary = "教师修改了译文，请以教师最终译文为准。"
    elif teacher_decision == "retranslate":
        problem_summary = "教师要求重新翻译本段。"
    else:
        problem_summary = "教师判定通过。"
    revision_instruction = ""
    if has_change:
        revision_instruction = "已保存教师最终译文，确认后作为可学习的最终结果。"
    elif teacher_decision == "retranslate":
        revision_instruction = "建议重新翻译本段后再提交审校。"
    elif teacher_decision == "pass":
        revision_instruction = "译文通过，无需修改。"

    return {
        "unit_id": None,
        "decision": teacher_decision,
        "error_types": error_types,
        "severity": severity,
        "problem_summary": problem_summary,
        "revision_instruction": revision_instruction,
        "teacher_revision": revision,
        "teacher_raw_comment": comment,
        "normalized_comment": comment or (problem_summary if problem_summary != "教师判定通过。" else ""),
        "translation_standards": [],
        "term_changes": [],
        "has_conflict": False,
        "conflict_fields": [],
        "conflict_explanation": "",
    }


def _parse_standardized_review_json(content: str, fallback: dict) -> tuple[dict, bool]:
    """从 LLM 返回文本中提取 JSON 对象；失败时返回 fallback 和 False。"""
    import json
    # 找到第一个 { 和最后一个 }，尽量兼容模型附带说明文字
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return fallback, False
    try:
        data = json.loads(content[start:end + 1])
        if not isinstance(data, dict):
            return fallback, False
        # 确保关键字段存在
        data.setdefault("decision", fallback.get("decision"))
        data.setdefault("error_types", fallback.get("error_types", []))
        data.setdefault("severity", fallback.get("severity"))
        data.setdefault("problem_summary", "")
        data.setdefault("revision_instruction", "")
        data.setdefault("teacher_revision", fallback.get("teacher_revision", ""))
        data.setdefault("teacher_raw_comment", fallback.get("teacher_raw_comment", ""))
        data.setdefault("normalized_comment", "")
        data.setdefault("translation_standards", [])
        data.setdefault("term_changes", [])
        data.setdefault("has_conflict", False)
        data.setdefault("conflict_fields", [])
        data.setdefault("conflict_explanation", "")
        return data, True
    except (ValueError, TypeError):
        return fallback, False



def generate_review_bundle(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict],
    name_hits: list[dict],
) -> dict:
    """生成「直接翻译 / 修正 / 最终仲裁 / 审校报告」结果包（双模式入口）。

    作用：按三个阶段公共函数依次执行，并汇总返回；保持旧调用兼容。
    """
    if not paragraphs:
        return _generate_mock_bundle(paragraphs, translations, term_hits, name_hits)

    direct_result = generate_direct_translations(
        paragraphs, term_hits, name_hits, fallback_translations=translations
    )
    corrected_result = generate_corrected_translations(
        paragraphs, translations, term_hits, name_hits
    )
    final_result = generate_final_arbitration(
        paragraphs,
        direct_result["translations"],
        corrected_result["translations"],
        term_hits,
        name_hits,
    )

    bundle = {
        "report": final_result.get("report", ""),
        "direct_translations": direct_result["translations"],
        "direct_degraded": direct_result.get("degraded", False),
        "corrected_translations": corrected_result["translations"],
        "corrected_degraded": corrected_result.get("degraded", False),
        "final_translations": final_result.get("final_translations", []),
        "final_degraded": final_result.get("final_degraded", False),
        "tradeoff_notes": final_result.get("tradeoff_notes", ""),
        "tradeoff_degraded": final_result.get("tradeoff_degraded", False),
    }
    return bundle


def _parse_numbered_translations_with_status(
    text: str, paragraphs: list[str], fallback: list[str]
) -> tuple[list[str], bool]:
    """解析某轮译文，并返回“是否发生了降级回退”。

    作用：与 _parse_numbered_translations 相同，但额外返回布尔值，
          供阶段 4 明确标注降级结果。
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

def _generate_mock_bundle(
    paragraphs: list[str],
    translations: list[str],
    term_hits: list[dict],
    name_hits: list[dict],
) -> dict:
    """生成占位版「四结果 + 审校报告」（mock / 缺密钥回退；不联网）。

    作用：报告文案与阶段 1 逐字节一致；直接翻译/修正结果/最终结果都用
          独立占位文案，让页面清楚看到“当前不是真实 LLM 多轮结果”——
          避免把原始 API 译文伪装成 LLM 结果。
    输入：paragraphs / translations / term_hits / name_hits —— 同公开入口。
    输出：dict —— 含 report / direct_translations / corrected_translations /
          final_translations。
    """
    return {
        "report": _generate_mock_report(paragraphs, term_hits, name_hits),
        "direct_translations": [
            f"（占位直接翻译·第{i + 1}段）待接入 LLM 直接翻译"
            for i in range(len(paragraphs))
        ],
        "corrected_translations": [
            f"（占位修正译文·第{i + 1}段）待接入 LLM 修正"
            for i in range(len(paragraphs))
        ],
        "final_translations": [
            f"（占位最终结果·第{i + 1}段）待接入 LLM 仲裁"
            for i in range(len(paragraphs))
        ],
        "tradeoff_notes": "（占位）翻译取舍说明：待接入 LLM 最终仲裁后生成。",
    }


def _parse_review_bundle(
    content: str, paragraphs: list[str], corrected_translations: list[str]
) -> dict:
    """把“最终仲裁”回复拆成 最终结果 / 翻译取舍说明 / 审校报告。

    作用：这就是最终仲裁那一次 LLM 调用的解析函数。它只负责解析：
          - `## 最终结果`：最终译文列表；
          - `## 翻译取舍说明`：模型对不同翻译版本取舍和原因；
          - `## 审校报告`：8 项报告。
          直接翻译和修正结果已经在各自独立调用中解析完成，所以这里不再
          解析它们，避免职责混杂。
    输入：content —— 最终仲裁 LLM 回复全文；
          paragraphs —— 阿语段落列表；
          corrected_translations —— 修正结果（最终结果解析失败时兜底）。
    输出：dict —— 含 report / final_translations / tradeoff_notes。
    """
    def _section_text(start_marker: str, end_marker: str) -> str | None:
        """取 start_marker 到 end_marker 之间的文本；缺少标记返回 None。"""
        if start_marker not in content or (end_marker and end_marker not in content):
            return None
        part = content.split(start_marker, 1)[1]
        if end_marker:
            part = part.split(end_marker, 1)[0]
        return part

    # 完全没有最终仲裁结构标记时，整体回退为“旧版单段报告”
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

    # 最终结果：在“## 最终结果”到“## 翻译取舍说明”/“## 审校报告”之间
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

    # 翻译取舍说明：在“## 翻译取舍说明”到“## 审校报告”之间
    tradeoff_text = _section_text(_TRADEOFF_SECTION_MARKER, _REPORT_SECTION_MARKER)
    if tradeoff_text is not None and tradeoff_text.strip():
        tradeoff_notes = tradeoff_text.strip()
        tradeoff_degraded = False
    else:
        tradeoff_notes = "（缺省）模型未输出翻译取舍说明，请人工复核直接翻译与修正结果。"
        tradeoff_degraded = True

    # 审校报告：取“## 审校报告”之后全部内容
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
    # (?s) 让 . 匹配换行；(.*?) 非贪婪；(?=...) 下一条段号或二级标题处截断
    pattern = re.compile(
        r"第(\d+)段[:：](.*?)(?=\n\s*第\d+段[:：]|\n\s*## |\Z)", re.S
    )
    matches = pattern.findall(text)
    # 解析出的条数必须与原段落数一致才算可信；否则旧数据兜底
    if len(matches) != len(paragraphs):
        return list(translations)

    result = []
    for number_str, segment in matches:
        # 去掉行内常见的 markdown 加粗/斜体标记，保留正文
        clean = re.sub(r"[*_#>`]", "", segment).strip()
        result.append(clean)
    return result


def _generate_mock_report(
    paragraphs: list[str], term_hits: list[dict], name_hits: list[dict]
) -> str:
    """生成占位审校报告（mock 模式 / 缺密钥回退；不联网）。

    作用：文案与阶段 1 逐字节一致，保证阶段 1 的旧测试断言零改动。
    输入：paragraphs —— 阿语段落列表；term_hits / name_hits ——
          术语/专名命中列表。
    输出：str —— 占位报告文本（含统计数字）。
    """
    # 统计口径：条数 = 去重后的词条数（len），次数 = 全部命中累计
    # （count 求和；hit.get("count", 1) 兼容无 count 字段的旧结构）
    term_total = sum(hit.get("count", 1) for hit in term_hits)
    name_total = sum(hit.get("count", 1) for hit in name_hits)
    return (
        "（占位）审校报告：待接入 LLM 后生成。\n\n"
        f"本次共 {len(paragraphs)} 段，术语命中 {len(term_hits)} 条（共 {term_total} 次），"
        f"专名命中 {len(name_hits)} 条（共 {name_total} 次）。"
    )


# ---------------------------------------------------------------------------
# 提示词模板：加载与数据填充
# ---------------------------------------------------------------------------

def _load_prompt_template(
    path: Path | None = None, placeholders: tuple[str, ...] | None = None
) -> str:
    """读取提示词模板并做结构校验。

    作用：读取指定模板（UTF-8）并校验结构：
          - 「## 系统消息」与「## 用户消息」两个分隔标记都必须存在
            （错误消息点名缺哪个），且系统标记在用户标记之前；
          - placeholders 指定的占位符必须全部位于「用户消息段」内且各恰好 1 次。
            注意：校验对象是「按用户标记拆出的用户段」而不是全模板——
            若占位符被误挪进系统消息段（或标记位置异常），全模板计数
            仍可能恰好为 1，但实际发给 LLM 的用户消息会残留字面
            占位符或为空。
    输入：path —— 模板路径；缺省读 _PROMPT_PATH；placeholders —— 占位符集合。
    输出：str —— 模板全文。
    异常：文件缺失抛 FileNotFoundError（app.py 已捕获，提示数据文件
          缺失）；结构非法抛 ValueError（中文消息，点名缺失的标记）。
    """
    if path is None:
        path = _PROMPT_PATH
    if placeholders is None:
        placeholders = _PLACEHOLDERS

    # 显式 UTF-8 读取（Windows 默认 GBK，模板是中文内容必须显式编码）
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()

    # 两个分隔标记都必须存在：_render_user_message / _review_with_api
    # 靠它们拆段，任一缺失即模板非法（错误消息点名缺哪个标记）
    if _SYSTEM_SECTION_MARKER not in template:
        raise ValueError(f"提示词模板缺少「{_SYSTEM_SECTION_MARKER}」分隔标记")
    if _USER_SECTION_MARKER not in template:
        raise ValueError(f"提示词模板缺少「{_USER_SECTION_MARKER}」分隔标记")

    # 标记顺序：str.find(sub) 返回子串首次出现的下标；
    # 系统标记必须在用户标记之前，否则拆出的「系统段」会混进用户数据
    if template.find(_SYSTEM_SECTION_MARKER) > template.find(_USER_SECTION_MARKER):
        raise ValueError("提示词模板中「## 系统消息」必须在「## 用户消息」之前")

    # 占位符校验：只针对用户消息段（用户标记之后的内容）逐个数数；
    # str.count(sub) 统计子串出现次数，每个占位符必须恰好 1 次
    # （标记存在已在上方校验，split(…, 1)[1] 必然存在）
    user_section = template.split(_USER_SECTION_MARKER, 1)[1]
    for name in placeholders:
        if user_section.count(f"{{{name}}}") != 1:
            raise ValueError(
                f"提示词模板用户消息段中占位符 {name!r} 必须恰好出现 1 次"
            )
    return template


def _format_hits_text(
    term_hits: list[dict] | None, name_hits: list[dict] | None
) -> str:
    """把术语/专名命中列表统一格式化为清单文本。

    作用：把 glossary.scan_glossary 的命中结果整理成一段人读的命中清单，
          填入提示词模板的用户消息，作为 LLM 审校的术语/专名依据。
          术语与专名两库表头不同（六列 vs 四列），这里用 .get 兼容键差异
          （专名无「领域」「处理方式」两列，不输出，不会 KeyError）。
    输入：term_hits —— 术语命中列表（每条含「阿语原文」「中文译文」
          「类别」「领域」「备注」「处理方式」与 "paragraphs" 段落号
          列表、"count" 次数）；name_hits —— 专名命中列表（结构同上，
          但无「领域」「处理方式」两列）。可为 None。
    输出：str —— 命中清单文本。格式：
              【术语命中】共 N 条
              - 阿语原文「…」→ 中文译文「…」（类别：…；领域：…；
                备注：…；处理方式：…；出现段落：1, 4, 5；出现次数：7）
              【专名命中】共 M 条
              - …
          某类无命中输出「【术语命中】无」；处理方式保留 CSV 原始值
          （force_check 等），不做 UI 显示映射（映射是展示层的事）。
    """
    # 术语六列表头中「阿语原文/中文译文」单独放在行首，其余四列进括号；
    # 专名四列表头中只有「类别」「备注」两列进括号
    term_extra_keys = ("类别", "领域", "备注", "处理方式")
    name_extra_keys = ("类别", "备注")

    def _format_group(hits: list[dict], kind: str, extra_keys: tuple) -> str:
        # 无命中输出「无」（空列表与 None 都在调用处归一化为 []）
        if not hits:
            return f"【{kind}命中】无"
        lines = [f"【{kind}命中】共 {len(hits)} 条"]
        for hit in hits:
            # 括号内各列用「；」分隔；.get(key, "") 缺列补空串（脏数据兜底，
            # 专名缺「领域」「处理方式」也不会 KeyError）
            details = "；".join(f"{key}：{hit.get(key, '')}" for key in extra_keys)
            # 出现段落列表（如 [1, 4, 5]）转成 "1, 4, 5" 字符串；
            # 出现次数缺 count 字段时按 1 次计（与占位报告统计口径一致）
            paragraphs = ", ".join(str(p) for p in hit.get("paragraphs", []))
            count = hit.get("count", 1)
            lines.append(
                f"- 阿语原文「{hit.get('阿语原文', '')}」→ "
                f"中文译文「{hit.get('中文译文', '')}」"
                f"（{details}；出现段落：{paragraphs}；出现次数：{count}）"
            )
        return "\n".join(lines)

    # 术语节 + 专名节，两节之间用换行连接
    term_section = _format_group(term_hits or [], "术语", term_extra_keys)
    name_section = _format_group(name_hits or [], "专名", name_extra_keys)
    return f"{term_section}\n{name_section}"


def _format_numbered(items: list[str], label: str) -> str:
    """把列表逐项编号化输出，让 LLM 能按段号引用。

    作用：把段落/译文列表转成「第N段：…」/「第N段译文：…」的多行文本，
          段号 1 基编号。审校报告的第 4 项要求「段号 + 原文引文 + 译文」
          引用，编号后 LLM 才能精确指认某个段落。
    输入：items —— 字符串列表；label —— 编号后的单位名称（如「段」）。
    输出：str —— 每项一行、以「第N+label：」开头的多行文本；
          空列表返回空串。
    """
    # 列表推导式：enumerate 从 1 开始编号，拼出「第N段：文本」一行
    return "\n".join(f"第{i}{label}：{item}" for i, item in enumerate(items, start=1))


def _render_user_message(template: str, replacements: dict[str, str]) -> str:
    """把提示词模板的用户消息段中的占位符替换为真实数据。

    作用：模板以「## 用户消息」为界——前半是系统消息（不替换），
          后半是用户消息（含该模板声明的占位符）。本函数只对后半做替换，
          返回替换后的用户消息文本。
    输入：template —— 模板全文；replacements —— 形如
          {"source_paragraphs": "…", "translations": "…"} 的替换字典。
    输出：str —— 替换完成后的用户消息文本。
    异常：模板中找不到「## 用户消息」标记时抛 ValueError。
    """
    # str.split(marker)：按标记把模板切成多段；标记之后的第一段即用户消息
    parts = template.split(_USER_SECTION_MARKER)
    if len(parts) < 2:
        raise ValueError("提示词模板缺少「## 用户消息」标记")
    user_section = parts[1]

    # 每个占位符逐一做纯文本替换。刻意用 str.replace 而不用 format：
    # 模板正文里可能含有中文括号「（」「）」等字符，format 语法会把
    # 花括号内容当作格式字段解析（如 {（} 会直接报错），replace 是
    # 逐字节的纯文本替换，对模板内容零约束。
    for name, text in replacements.items():
        # f"{{{name}}}" 生成 "{name}" 字面量（花括号本身也是替换目标）
        user_section = user_section.replace(f"{{{name}}}", text)
    return user_section


# ---------------------------------------------------------------------------
# 请求构造与发送
# ---------------------------------------------------------------------------

def _build_api_url(config: ReviewConfig) -> str:
    """构造 LLM 接口地址（base URL + /chat/completions）。

    作用：DeepSeek 与 OpenAI 兼容接口的补全路径都是 /chat/completions；
          base URL 可能被用户填成带尾斜杠的写法，先 rstrip("/") 去掉，
          再拼接，避免出现 // 双斜杠。
    输入：config —— 审校配置（提供 api_url）。
    输出：str —— 形如 https://api.deepseek.com/chat/completions。
    """
    # str.rstrip("/")：去掉字符串末尾的全部斜杠（只去尾，不影响中间的）
    return config.api_url.rstrip("/") + "/chat/completions"


def _build_request_payload(
    config: ReviewConfig, system: str, user: str
) -> dict:
    """构造一次 LLM 审校请求的 JSON 请求体。

    作用：按 OpenAI 兼容接口（DeepSeek 同款）组装请求体——模型名、
          system/user 双角色消息与固定 temperature。请求体不包含任何
          密钥：密钥只出现在 HTTP 头 Authorization 里（见
          _review_with_api）。
    输入：config —— 审校配置（提供 model）；system —— 系统消息文本；
          user —— 用户消息文本（占位符已替换）。
    输出：dict —— 请求体，直接作为 requests.post(json=...) 的参数。
    """
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": _TEMPERATURE,
    }


def _call_llm(
    config: ReviewConfig,
    prompt_path: Path,
    placeholders: tuple[str, ...],
    replacements: dict[str, str],
) -> str:
    """执行一次独立的 LLM 调用（阶段 3.2 各阶段共用）。

    作用：按「读模板 → 拆系统/用户段 → 替换占位符 → 构造请求 → POST →
          → 解析响应」完成一次 LLM 调用。每个阶段传入自己的模板、占位符
          和替换数据，确保“直接翻译”阶段不会收到 API 译文等非本阶段输入。
    输入：config —— 审校配置；prompt_path —— 该阶段模板路径；
          placeholders —— 该模板的占位符集合；replacements —— 占位符替换字典。
    输出：str —— LLM 返回的原始 markdown 文本。
    异常：超时/连接失败 → ReviewNetworkError；HTTP 非 200 →
          ReviewBusinessError（附状态码与响应片段）；解析失败 →
          ReviewParseError；以上消息全部中文且不含密钥。
    """
    # 1. 读取本阶段模板（缺失 → FileNotFoundError；结构非法 → ValueError）
    template = _load_prompt_template(prompt_path, placeholders)

    # 2. 拆系统消息段：截到「## 系统消息」标记之后；模板头部说明不发给 LLM
    system_part = template.split(_USER_SECTION_MARKER)[0]
    system_message = system_part.split(_SYSTEM_SECTION_MARKER, 1)[1].strip()

    # 3. 只替换本阶段模板声明的占位符
    user_message = _render_user_message(template, replacements)

    # 4. 构造请求：URL（base + /chat/completions）与请求体
    url = _build_api_url(config)
    payload = _build_request_payload(config, system_message, user_message)

    # 5. 发送请求。密钥只放在 Authorization 请求头里；timeout 来自配置
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
        )
    except requests.exceptions.Timeout as e:
        raise ReviewNetworkError(
            f"审校请求超时（{config.timeout_seconds} 秒）"
        ) from e
    except requests.exceptions.RequestException as e:
        raise ReviewNetworkError(f"审校请求连接失败：{e}") from e

    # 6. HTTP 状态码非 200 视为业务层失败：带状态码与响应文本片段
    if response.status_code != 200:
        snippet = (response.text or "")[:200]
        raise ReviewBusinessError(
            f"审校接口返回 HTTP 状态码 {response.status_code}，响应片段：{snippet}",
            status_code=response.status_code,
            snippet=snippet,
        )

    # 7. 解析成功响应
    return _parse_review_response(response)


def _parse_review_response(response) -> str:
    """解析 LLM 审校接口的响应体。

    作用：把 OpenAI 兼容格式的响应逐层校验后取出报告文本：
          1) 非合法 JSON → ReviewParseError；
          2) 缺 choices 或 choices 不是列表 → ReviewParseError；
          3) 缺 message 或 message.content → ReviewParseError。
          成功响应形如 {"choices": [{"message": {"content": "报告"}}]}。
    输入：response —— requests 的响应对象（有 .json() 方法与 status_code）。
    输出：str —— LLM 生成的审校报告文本。
    """
    try:
        # response.json()：把响应体按 JSON 解析成 dict；
        # 响应体不是合法 JSON 时抛 ValueError（requests 的
        # JSONDecodeError 是它的子类）
        data = response.json()
    except ValueError as e:
        raise ReviewParseError("审校接口响应不是合法 JSON") from e

    # dict.get(key)：键不存在时返回 None，不抛 KeyError；
    # 逐层校验：choices → choices[0] → message → content
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ReviewParseError("审校接口响应缺少 choices 列表")
    first = choices[0]
    if not isinstance(first, dict):
        raise ReviewParseError("审校接口响应中 choices[0] 不是对象")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ReviewParseError("审校接口响应缺少 message 字段")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ReviewParseError("审校接口响应缺少 message.content 字段")
    return content
