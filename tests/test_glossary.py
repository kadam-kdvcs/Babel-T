# -*- coding: utf-8 -*-
"""pytest 测试：modules.glossary（归一化 / 加载 / 扫描）。

运行方式
--------
在项目根目录执行 pytest（由主线程统一运行），例如：
    python -m pytest tests/test_glossary.py -v
（必须从根目录运行，才能 import modules 下的模块。）

约定
----
- 本文件只依赖 pytest 与标准库，不读取项目 data/ 下的真实数据文件；
- load 相关测试用 pytest 的 tmp_path fixture 自建临时 CSV，
  写文件统一用 open(..., "w", encoding="utf-8-sig", newline="")；
- scan 相关测试直接手写词条 dict（结构与 load_glossary 输出一致），
  与文件系统解耦。
"""

import csv

import pytest

# 从项目根目录 import 模块（与主线程 pytest 的运行方式一致）。
from modules.glossary import normalize_arabic, load_glossary, scan_glossary


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _write_csv(path, header, rows):
    """测试辅助：把表头与数据行写入 CSV 文件。

    参数
    ----
    path   : pathlib.Path —— 目标文件（tmp_path 下的临时文件）；
    header : list[str]    —— 表头行；
    rows   : list[list[str]] —— 数据行。

    说明
    ----
    与 load_glossary 的读取约定保持一致：
    open 用 encoding="utf-8-sig"（写入 BOM）与 newline=""。
    """
    # open(path, "w", encoding="utf-8-sig", newline="")：
    #   utf-8-sig 写入时会在文件头加 BOM（﻿），正好覆盖 BOM 读取测试；
    #   newline="" 在 Windows 下不把 \n 改写成 \r\n，保证写入内容可预期。
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        # csv.writer(f)：把行序列化为 CSV；writerow() 一次写一行。
        # 字段内含逗号时自动整体加引号（恰好用于「带引号字段」测试）。
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# normalize_arabic 测试
# ---------------------------------------------------------------------------

def test_normalize_removes_diacritics():
    """含全部 12 种变音符的字符串，变音符应全部被删除、字母保留。"""
    # 12 个变音符：ً ٌ ٍ َ ُ ِ ّ ْ（ً-ْ）与 ٓ ٔ ٕ ٰ（ٓ-ٕ、ٰ）。
    # 用「ب + 变音符」的组合逐项列出，删除变音符后应只剩 12 个 ب。
    text = (
        "بً"  # بً   fatha tanween
        "بٌ"  # بٌ   damma tanween
        "بٍ"  # بٍ   kasra tanween
        "بَ"  # بَ   fatha
        "بُ"  # بُ   damma
        "بِ"  # بِ   kasra
        "بّ"  # بّ   shadda
        "بْ"  # بْ   sukūn
        "بٓ"  # بٓ   madda above
        "بٔ"  # بٔ   hamza above
        "بٕ"  # بٕ   hamza below
        "بٰ"  # بٰ   superscript alef
    )
    assert normalize_arabic(text) == "ب" * 12


def test_normalize_removes_tatweel():
    """延长符 ـ（ـ）应被删除："سلامــة" → "سلامة"。"""
    # "سلامــة"：م 与 ة 之间夹着三个延长符 ـ（ـ）。
    assert normalize_arabic("سلامــة") == "سلامة"


def test_normalize_removes_bidi_controls():
    """8 种双向控制符（‎-‮ 与 ؜）应全部被删除。"""
    # 在 "سلام عليك" 的字符之间穿插全部 8 个控制符，删除后应还原为原文。
    text = "س‎ل‏ا‪م‫ ع‬ل‭ي‮ك؜"
    assert normalize_arabic(text) == "سلام عليك"


def test_normalize_unifies_alef_variants():
    """أ إ آ ٱ 四种 alef 变体应全部折叠为标准 ا。"""
    # أ 上 hamza、إ 下 hamza、آ madda、ٱ alef wasla。
    text = "أ إ آ ٱ"
    assert normalize_arabic(text) == "ا ا ا ا"


def test_normalize_keeps_ya_and_taa_marbuta():
    """刻意不做 ى→ي、ة→ه：含 ة/ي/ى 的词应原样保留。"""
    # "سنة"（年）含 ة（taa marbuta），若做 ة→ه 会变成 "سنه"；
    # "كرسي"（椅子）与 "موسى"（人名）含 ي 与 ى（alef maksura），
    # 若做 ى→ي 会改变字形。三者都应原样不变。
    assert normalize_arabic("سنة") == "سنة"
    assert normalize_arabic("كرسي") == "كرسي"
    assert normalize_arabic("موسى") == "موسى"


def test_normalize_keeps_medial_hamza():
    """中位 hamza（ؤ/ئ）是词义一部分，应原样保留。"""
    # مؤتمر（会议）、رئيس（总统）：删除 ؤ/ئ 会破坏词义，模块刻意不处理。
    assert normalize_arabic("مؤتمر رئيس") == "مؤتمر رئيس"


def test_normalize_keeps_latin():
    """拉丁字母与数字不受影响。"""
    assert normalize_arabic("ABC 123") == "ABC 123"


def test_normalize_empty():
    """空字符串 → 空字符串。"""
    assert normalize_arabic("") == ""


# ---------------------------------------------------------------------------
# load_glossary 测试
# ---------------------------------------------------------------------------

# 六列表头：术语库 terms.csv（与项目 data/terms.csv 一致）。
_TERMS_HEADER = ["阿语原文", "中文译文", "类别", "领域", "备注", "处理方式"]
# 四列表头：专名库 proper_names.csv。
_NAMES_HEADER = ["阿语原文", "中文译文", "类别", "备注"]


def test_load_terms_six_columns(tmp_path):
    """六列表头 + 2 行数据 → 2 条 dict，键为六个表头名，值正确。"""
    # tmp_path：pytest 内置 fixture，每个测试独享一个临时目录，结束自动清理。
    csv_file = tmp_path / "terms.csv"
    _write_csv(
        csv_file,
        _TERMS_HEADER,
        [
            ["السلام", "和平", "常用词", "政治", "常用问候语", "suggest"],
            ["مؤتمر", "会议", "常用词", "政治", "", "force_check"],
        ],
    )
    entries = load_glossary(csv_file)
    assert len(entries) == 2
    # 键 = CSV 原表头名（六个），值应逐列对应。
    assert entries[0] == {
        "阿语原文": "السلام",
        "中文译文": "和平",
        "类别": "常用词",
        "领域": "政治",
        "备注": "常用问候语",
        "处理方式": "suggest",
    }
    assert entries[1] == {
        "阿语原文": "مؤتمر",
        "中文译文": "会议",
        "类别": "常用词",
        "领域": "政治",
        "备注": "",
        "处理方式": "force_check",
    }


def test_load_names_four_columns(tmp_path):
    """四列表头 + 1 行 → 键为四个表头名。"""
    csv_file = tmp_path / "proper_names.csv"
    _write_csv(
        csv_file,
        _NAMES_HEADER,
        [["الأمم المتحدة", "联合国", "机构名", ""]],
    )
    entries = load_glossary(csv_file)
    assert len(entries) == 1
    assert entries[0] == {
        "阿语原文": "الأمم المتحدة",
        "中文译文": "联合国",
        "类别": "机构名",
        "备注": "",
    }


def test_load_utf8_bom(tmp_path):
    """带 UTF-8 BOM 的文件可正常读取，表头键不带 ﻿ 前缀。"""
    # _write_csv 用 utf-8-sig 写入，文件头自带 BOM（﻿）。
    # load_glossary 用 utf-8-sig 读取会自动剥离 BOM，键应为 "阿语原文"。
    csv_file = tmp_path / "terms_bom.csv"
    _write_csv(
        csv_file,
        _TERMS_HEADER,
        [["السلام", "和平", "常用词", "政治", "", "suggest"]],
    )
    entries = load_glossary(csv_file)
    assert len(entries) == 1
    assert "阿语原文" in entries[0]            # 键是干净的表头名
    assert "﻿阿语原文" not in entries[0]  # 不应带 BOM 前缀
    assert entries[0]["阿语原文"] == "السلام"


def test_load_missing_file(tmp_path):
    """文件不存在 → FileNotFoundError（由调用方处理，模块不捕获）。"""
    # pytest.raises(...)：断言其上下文内必须抛出指定异常，否则测试失败。
    with pytest.raises(FileNotFoundError):
        load_glossary(tmp_path / "not_exist.csv")


def test_load_skips_empty_arabic_rows(tmp_path):
    """阿语原文为空的行应被跳过。"""
    csv_file = tmp_path / "terms.csv"
    _write_csv(
        csv_file,
        _TERMS_HEADER,
        [
            # 第一行阿语原文为空（模拟手工编辑遗留的空行）。
            ["", "无原文", "常用词", "政治", "", "suggest"],
            ["السلام", "和平", "常用词", "政治", "", "suggest"],
        ],
    )
    entries = load_glossary(csv_file)
    assert len(entries) == 1
    assert entries[0]["阿语原文"] == "السلام"


def test_load_quoted_fields(tmp_path):
    """字段值内含逗号且带引号 → 应解析为一个完整字段。"""
    csv_file = tmp_path / "terms.csv"
    # 备注字段 "حرب, أهلية"（含逗号）会被 csv.writer 自动整体加引号；
    # 读取后应还原为一个字段，而不是被拆成两列。
    _write_csv(
        csv_file,
        _TERMS_HEADER,
        [["حرب أهلية", "内战", "常用词", "军事", "حرب, أهلية", "context_warning"]],
    )
    entries = load_glossary(csv_file)
    assert len(entries) == 1
    assert entries[0]["备注"] == "حرب, أهلية"
    assert entries[0]["阿语原文"] == "حرب أهلية"


def test_load_empty_file(tmp_path):
    """只有表头（无数据行）→ 空列表 []。"""
    csv_file = tmp_path / "terms.csv"
    _write_csv(csv_file, _TERMS_HEADER, [])
    assert load_glossary(csv_file) == []


# ---------------------------------------------------------------------------
# scan_glossary 测试
# ---------------------------------------------------------------------------

def test_scan_basic_hit_fields():
    """一段命中：命中 dict 应含词条原字段 + paragraphs + count。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "政治",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    hits = scan_glossary(["نحن نحب السلام في كل مكان"], entries)
    assert len(hits) == 1
    hit = hits[0]
    # 命中应完整保留原词条字段（键 = CSV 表头名）。
    assert hit["阿语原文"] == "السلام"
    assert hit["中文译文"] == "和平"
    assert hit["类别"] == "常用词"
    assert hit["备注"] == ""
    # 新增字段：paragraphs（出现段落号列表）与 count（全文累计次数）。
    assert hit["paragraphs"] == [1]
    assert hit["count"] == 1


def test_scan_variant_spelling():
    """正文带变音符仍可命中不带变音符的词条（归一化后匹配）。"""
    # 正文 "نؤكد على السَّلام" 含叠音符 shadda（ّ），词条 "السلام" 不带；
    # 归一化删除变音符后双方一致，应命中。
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    hits = scan_glossary(["نؤكد على السَّلام"], entries)
    assert len(hits) == 1
    assert hits[0]["count"] == 1


def test_scan_paragraph_no():
    """命中出现在第 2 段 → paragraphs == [2]。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    paragraphs = [
        "هذا النص الأول لا يحتوي على الكلمة المستهدفة",
        "وفي النهاية نلتزم بالسلام الدائم",
    ]
    hits = scan_glossary(paragraphs, entries)
    assert len(hits) == 1
    assert hits[0]["paragraphs"] == [2]
    assert hits[0]["count"] == 1


def test_scan_count_multiple_occurrences():
    """同一段出现 2 次 → 只有 1 条命中且 count == 2。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    hits = scan_glossary(["السلام والسلام", "بدون كلمات"], entries)
    assert len(hits) == 1
    assert hits[0]["count"] == 2
    assert hits[0]["paragraphs"] == [1]


def test_scan_multiword_term():
    """多词词条（含空格）可命中。"""
    entries = [
        {
            "阿语原文": "الأمم المتحدة",
            "中文译文": "联合国",
            "类别": "机构名",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    hits = scan_glossary(["عملت الأمم المتحدة على تحقيق السلام"], entries)
    assert len(hits) == 1
    assert hits[0]["count"] == 1
    assert hits[0]["中文译文"] == "联合国"


def test_scan_substring_no_word_boundary():
    """纯子串匹配（阶段 1 预期行为）：词条命中派生词。"""
    # 词条 "فلسطين" 是 "الفلسطينية"（巴勒斯坦的，形容词）的子串，
    # 阶段 1 不做词边界判断，命中是预期行为。
    entries = [
        {
            "阿语原文": "فلسطين",
            "中文译文": "巴勒斯坦",
            "类别": "专名",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    hits = scan_glossary(["القضية الفلسطينية مهمة"], entries)
    assert len(hits) == 1
    assert hits[0]["count"] == 1


def test_scan_no_matches():
    """无命中 → []。"""
    entries = [
        {
            "阿语原文": "فلسطين",
            "中文译文": "巴勒斯坦",
            "类别": "专名",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    assert scan_glossary(["لا توجد كلمات مذكورة هنا"], entries) == []


def test_scan_empty_paragraphs():
    """空段落列表 → []。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    assert scan_glossary([], entries) == []


def test_scan_empty_entries():
    """空词条列表 → []。"""
    assert scan_glossary(["السلام في النص"], []) == []


def test_scan_sorted_by_paragraph():
    """两个词条分别在段 1、段 2 命中 → 按首次出现段落号升序排列。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        },
        {
            "阿语原文": "مؤتمر",
            "中文译文": "会议",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "force_check",
        },
    ]
    paragraphs = ["السلام في الفقرة الأولى", "مؤتمر في الفقرة الثانية"]
    hits = scan_glossary(paragraphs, entries)
    assert len(hits) == 2
    # 按首次出现段落号升序：第 1 段的命中在前，第 2 段的命中在后。
    assert [h["paragraphs"][0] for h in hits] == [1, 2]


def test_scan_aggregates_across_paragraphs():
    """同一词条跨多段出现 → 只有 1 条命中，段落号合并、次数累计。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    paragraphs = [
        "نلتزم بالسلام في الفقرة الأولى",
        "لا كلمات هنا",
        "نلتزم بالسلام مرة أخرى",
        "لا شيء",
        "والسلام في الأخيرة",
    ]
    hits = scan_glossary(paragraphs, entries)
    assert len(hits) == 1          # 不产生重复词条行
    assert hits[0]["count"] == 3   # 全文累计 3 次
    assert hits[0]["paragraphs"] == [1, 3, 5]  # 段落号合并为升序列表


def test_scan_mixed_counts():
    """同一词条在某段出现 2 次、另一段出现 1 次 → 累计为 3，段落号去重。"""
    entries = [
        {
            "阿语原文": "السلام",
            "中文译文": "和平",
            "类别": "常用词",
            "领域": "",
            "备注": "",
            "处理方式": "suggest",
        }
    ]
    paragraphs = ["السلام والسلام في فقرة", "لا شيء", "والسلام هنا"]
    hits = scan_glossary(paragraphs, entries)
    assert len(hits) == 1
    assert hits[0]["count"] == 3
    assert hits[0]["paragraphs"] == [1, 3]
