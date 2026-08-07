"""审校报告模块（阶段 1 占位实现）。

本阶段不调用 LLM，只生成占位报告并回填流水线统计数字，用于验证
「切分 → 扫描 → 译文 → 报告」链路贯通。未来阶段（阶段 3）将读取
prompts/review_report_prompt.md 提示词模板调用 LLM：
保持函数签名不变，仅替换内部实现。
"""


def generate_review_report(paragraphs: list[str], translations: list[str], term_hits: list[dict], name_hits: list[dict]) -> str:
    """生成审校报告文本（markdown 格式）。

    作用：审校流水线的出口。阶段 1 不调用 LLM，仅把各环节的统计数字
          （段落数、术语命中数、专名命中数）回填进一段占位报告，
          用于验证「切分 → 扫描 → 译文 → 报告」整条链路的数据贯通，
          并让 Streamlit 前端可以先渲染出报告。

    输入：paragraphs —— list[str]，阿语段落列表；
          translations —— list[str]，与段落一一对应的译文列表；
          term_hits —— list[dict]，术语命中列表，元素为命中的结构化信息
          （例如 {"term": 词条, "paragraph_index": 段号}），阶段 1 仅统计数量；
          name_hits —— list[dict]，专名命中列表，元素结构同 term_hits。

    输出：str —— 报告文本，markdown 格式，可在 Streamlit 中直接渲染；
          各列表为空时返回仅含统计数字（0 段、0 条）的占位报告，不报错。

    未来接口约定（阶段 3 不变）：同一组输入，返回完整审校报告字符串，
    函数签名保持 generate_review_report(...) -> str 不变；阶段 3 仅把内部
    实现替换为读取 prompts/review_report_prompt.md 提示词模板并调用 LLM，
    所有调用方无需改动。
    """
    # 统计口径：条数 = 去重后的词条数（len），次数 = 全部命中累计（count 求和）
    # hit.get("count", 1)：兼容旧结构（无 count 字段时按 1 次计）
    term_total = sum(hit.get("count", 1) for hit in term_hits)
    name_total = sum(hit.get("count", 1) for hit in name_hits)
    return (
        "（占位）审校报告：待接入 LLM 后生成。\n\n"
        f"本次共 {len(paragraphs)} 段，术语命中 {len(term_hits)} 条（共 {term_total} 次），"
        f"专名命中 {len(name_hits)} 条（共 {name_total} 次）。"
    )
