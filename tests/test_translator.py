"""translator 模块的单元测试。

测试对象：modules.translator.translate_paragraphs（阶段 1 占位实现）。
运行方式：在项目根目录执行 `python -m pytest tests/test_translator.py -v`
（从根目录运行才能 import modules 下的模块）。
阶段 2 接入真实翻译 API 后，本文件的断言可保持不变。
"""

from modules.translator import translate_paragraphs


def test_placeholder_length_matches():
    """验证译文数量与输入段落数一致（3 段 → 3 条译文）。

    作用：确认占位翻译「一一对应」的数据流特性，N 段输入必然产生 N 条译文。
    输入：3 段阿语段落列表（阶段 1 不解析阿语内容，占位文本即可）。
    输出：translate_paragraphs 返回含 3 条译文的列表；断言失败即测试失败。
    """
    # 构造 3 段占位输入段落
    paragraphs = ["第1段", "第2段", "第3段"]
    # 调用被测函数，返回与输入一一对应的译文列表
    translations = translate_paragraphs(paragraphs)
    # 断言译文数量与输入段数一致
    assert len(translations) == 3


def test_placeholder_contains_marker():
    """验证译文包含「占位译文」标记字样。

    作用：确认生成的是占位译文而非空串，且标记文案正确，便于前端识别占位状态。
    输入：1 段阿语段落。
    输出：译文首元素为包含「占位译文」的非空字符串；断言失败即测试失败。
    """
    # 构造单段输入
    paragraphs = ["测试段落"]
    # 调用被测函数
    translations = translate_paragraphs(paragraphs)
    # 断言占位标记「占位译文」出现在第一条译文中
    assert "占位译文" in translations[0]


def test_empty_input():
    """验证空输入返回空列表（[] → []）。

    作用：确认边界行为——空段落列表不应报错，应返回空译文列表 []。
    输入：空列表 []。
    输出：[]；断言失败即测试失败。
    """
    # 调用被测函数，输入为空列表
    result = translate_paragraphs([])
    # 断言返回空列表
    assert result == []
