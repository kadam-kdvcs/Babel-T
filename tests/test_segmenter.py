"""段落切分模块的单元测试。

运行方式：在项目根目录执行 `python -m pytest tests/test_segmenter.py -v`
（从根目录运行才能 import modules 下的模块）。
"""

from modules.segmenter import segment_paragraphs


def test_empty_text():
    """空文本应返回空列表"""
    assert segment_paragraphs("") == []


def test_whitespace_only_text():
    """纯空白文本应返回空列表"""
    assert segment_paragraphs("   \n \n  ") == []


def test_single_line_is_one_paragraph():
    """只有一行的文本应返回一段"""
    assert segment_paragraphs("今天开了一场会议") == ["今天开了一场会议"]


def test_multiple_lines_no_blank_each_line_is_paragraph():
    """全文无空行时，按换行分段，一行一段"""
    text = "第一行内容\n第二行内容\n第三行内容"
    assert segment_paragraphs(text) == ["第一行内容", "第二行内容", "第三行内容"]


def test_blank_line_separates_paragraphs():
    """空行作为分隔符，把文本分成多段"""
    text = "第一段\n\n第二段\n\n\n第三段"
    assert segment_paragraphs(text) == ["第一段", "第二段", "第三段"]


def test_windows_line_endings():
    """Windows 换行符 \\r\\n 应被正确处理，段内不留 \\r"""
    text = "第一段\r\n第二行\r\n\r\n第二段\r\n"
    paragraphs = segment_paragraphs(text)
    assert paragraphs == ["第一段\n第二行", "第二段"]
    assert all("\r" not in p for p in paragraphs)


def test_blank_line_with_spaces_is_separator():
    """只含空白的行也算空行，是分隔符"""
    assert segment_paragraphs("第一段\n   \n第二段") == ["第一段", "第二段"]


def test_trailing_blank_lines_dropped():
    """末尾的空行不产生多余段落"""
    assert segment_paragraphs("第一段\n\n\n") == ["第一段"]


def test_internal_single_newline_kept():
    """有空行时，段内部的单换行要保留"""
    assert segment_paragraphs("第一行\n第二行\n\n第三段") == ["第一行\n第二行", "第三段"]


def test_paragraphs_stripped():
    """每段首尾空白应被去除"""
    assert segment_paragraphs("  第一段  \n\n  第二段  ") == ["第一段", "第二段"]
