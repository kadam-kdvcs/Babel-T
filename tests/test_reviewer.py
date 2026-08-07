"""reviewer 模块的单元测试。

测试对象：modules.reviewer.generate_review_report（阶段 1 占位实现）。
运行方式：在项目根目录执行 `python -m pytest tests/test_reviewer.py -v`
（从根目录运行才能 import modules 下的模块）。
阶段 3 接入 LLM 后，本文件的断言可保持不变。
"""

from modules.reviewer import generate_review_report


def test_report_contains_counts():
    """验证报告文本中包含流水线统计数字（去重条数 + 累计次数）。

    作用：确认占位报告把段落数、术语命中条数（去重）与累计次数、
          专名命中条数（去重）与累计次数回填进了文本，
          即「切分 → 扫描 → 译文 → 报告」链路的数据已贯通到报告层。
    输入：2 段、术语命中 3 条（count 分别为 2/1/1，累计 4 次）、
          专名命中 1 条（count 为 2，累计 2 次）。
    输出：报告字符串分别包含「2 段」「术语命中 3 条（共 4 次）」
          「专名命中 1 条（共 2 次）」；任一断言失败即测试失败。
    """
    # 构造 2 段阿语段落与对应的 2 条译文
    paragraphs = ["段落A", "段落B"]
    translations = ["译文A", "译文B"]
    # 构造 3 条术语命中（累计 2+1+1=4 次）与 1 条专名命中（累计 2 次）
    term_hits = [{"term": "术语1", "count": 2}, {"term": "术语2", "count": 1}, {"term": "术语3", "count": 1}]
    name_hits = [{"name": "专名1", "count": 2}]
    # 调用被测函数，生成占位审校报告
    report = generate_review_report(paragraphs, translations, term_hits, name_hits)
    # 断言报告文本包含条数与次数两类统计数字
    assert "2 段" in report
    assert "术语命中 3 条（共 4 次）" in report
    assert "专名命中 1 条（共 2 次）" in report


def test_report_contains_placeholder_marker():
    """验证报告包含「占位」标记字样。

    作用：确认返回的是阶段 1 占位报告而非空串，便于前端识别占位状态。
    输入：1 段、术语命中 0 条、专名命中 0 条。
    输出：报告字符串包含「占位」；断言失败即测试失败。
    """
    # 构造单段输入，命中列表为空，调用被测函数
    report = generate_review_report(["段落"], ["译文"], [], [])
    # 断言占位标记「占位」出现在报告中
    assert "占位" in report


def test_report_is_string():
    """验证返回值是 str 类型。

    作用：确认接口返回类型符合约定（str），可直接在 Streamlit 中渲染。
    输入：四个参数全部为空列表（边界情况）。
    输出：返回值为 str 类型；断言失败即测试失败。
    """
    # 调用被测函数，四个输入均为空列表
    report = generate_review_report([], [], [], [])
    # 断言返回类型为 str
    assert isinstance(report, str)
