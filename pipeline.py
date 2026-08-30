# -*- coding: utf-8 -*-
"""核心流水线：从原文到四结果 + 审校报告（无 Streamlit UI 依赖）。

作用：把 app.py 中的 run_pipeline / run_pipeline_progressive 拆出，
     供 Streamlit 页面或命令行直接调用。本模块不包含任何 st.* 控件。
"""

from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from modules import glossary, reviewer, segmenter, settings, translator

BASE_DIR = Path(__file__).parent

# 与 app.py 顶部一致：无论从哪个目录启动，都读取项目根目录的 .env
load_dotenv(BASE_DIR / ".env")


def run_pipeline(text: str) -> dict:
    """执行完整处理流水线（切分 → 扫描 → 翻译 → 报告）。

    作用：命令行/测试/无 UI 环境的完整入口。实现上委托给
          run_pipeline_progressive（不带任何回调）。
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
    """执行完整处理流水线，并支持“逐段完成即回调”。"""
    # 段落切分：整篇文本 → 段落列表
    paragraphs = segmenter.segment_paragraphs(text)
    if on_paragraphs is not None:
        on_paragraphs(paragraphs)

    # 加载术语库与专名库
    terms = glossary.load_glossary(BASE_DIR / "data" / "terms.csv")
    names = glossary.load_glossary(BASE_DIR / "data" / "proper_names.csv")

    # 扫描命中
    term_hits = glossary.scan_glossary(paragraphs, terms)
    name_hits = glossary.scan_glossary(paragraphs, names)

    # 翻译：mock 占位 / api 并发翻译，每段完成回调
    translations = translator.translate_paragraphs_parallel(
        paragraphs,
        term_hits,
        name_hits,
        on_translation=on_translation,
    )

    # 翻译模式与回退标志
    config = settings.load_translation_config()
    translation_mode = config.engine
    translation_fallback = (
        config.engine == settings.API_ENGINE and not config.has_credentials
    )

    # LLM 多轮处理前通知 UI
    if on_review_start is not None:
        on_review_start()

    # 审校结果包：直接翻译 / 修正 / 最终仲裁 / 取舍说明 / 审校报告
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

    # 审校模式与回退标志
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
        "direct_degraded": review_result.get("direct_degraded", False),
        "corrected_degraded": review_result.get("corrected_degraded", False),
        "final_degraded": review_result.get("final_degraded", False),
        "tradeoff_degraded": review_result.get("tradeoff_degraded", False),
        "term_hits": term_hits,
        "name_hits": name_hits,
        "report": report,
        "translation_mode": translation_mode,
        "translation_fallback": translation_fallback,
        "review_mode": review_mode,
        "review_fallback": review_fallback,
    }
