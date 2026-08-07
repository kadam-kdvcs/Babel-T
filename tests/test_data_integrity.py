"""数据文件完整性测试：给 data/ 下的示例数据上保险。

作用：防止有人误改数据文件（表头、条数、编码）导致页面或测试异常。
运行方式：项目根目录 `python -m pytest tests/test_data_integrity.py -v`。
"""

from pathlib import Path

# 项目根目录：本测试文件在 tests/ 下，上一级即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from modules.glossary import load_glossary
from modules.segmenter import segment_paragraphs


def test_terms_csv_loadable_and_sufficient():
    """术语库可加载且不少于 5 条，表头含六列关键字段。"""
    entries = load_glossary(PROJECT_ROOT / "data" / "terms.csv")
    assert len(entries) >= 5
    # 每条命中都应含术语库六列字段（表头即字段名）
    for entry in entries:
        for col in ["阿语原文", "中文译文", "类别", "领域", "备注", "处理方式"]:
            assert col in entry, f"terms.csv 词条缺少列：{col}"


def test_proper_names_csv_loadable_and_sufficient():
    """专名库可加载且不少于 3 条，表头含四列关键字段。"""
    entries = load_glossary(PROJECT_ROOT / "data" / "proper_names.csv")
    assert len(entries) >= 3
    for entry in entries:
        for col in ["阿语原文", "中文译文", "类别", "备注"]:
            assert col in entry, f"proper_names.csv 词条缺少列：{col}"


def test_sample_text_segments_into_paragraphs():
    """示例文本可切分出至少 4 段（保证演示效果）。"""
    sample_path = PROJECT_ROOT / "data" / "samples" / "politics_001.txt"
    text = sample_path.read_text(encoding="utf-8")
    paragraphs = segment_paragraphs(text)
    assert len(paragraphs) >= 4


def test_sample_text_contains_diacritics():
    """示例文本应含变音符（用于演示归一化命中）。"""
    sample_path = PROJECT_ROOT / "data" / "samples" / "politics_001.txt"
    text = sample_path.read_text(encoding="utf-8")
    # 常见变音符：فتحة َ、ضمة ُ、闪音 ّ、tanwin ً 等，出现任意一个即满足
    assert any(mark in text for mark in "ًٌٍَُِّْٰٕٓٔ")
