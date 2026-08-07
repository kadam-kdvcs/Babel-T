"""段落切分模块：把用户粘贴的整篇文本切成段落列表。

规则（阶段 1，从简）：
1. 文本中存在空行时，以空行为分隔符，一组为一段；
2. 全文没有任何空行时，按换行分段，一行即为一段；
3. 段落编号不做在本模块里（返回的列表索引即编号，展示层从 1 开始显示）。

本模块是纯函数模块：不读文件、不依赖全局状态、不涉及 UI，
输出只由输入 text 决定，便于单元测试。
"""


def segment_paragraphs(text: str) -> list[str]:
    """把整段文本切分为段落列表。

    作用：将粘贴的全文（可能含 Windows 换行 \\r\\n、空行、行首尾空格）
          按上述规则切成段落，供后续扫描与展示使用。

    输入：text —— 用户输入的全文，str 类型，可以为空。
    输出：list[str] —— 段落列表；每段已去除首尾空白；
          段落内部保留单换行（\\n）；
          空文本或纯空白文本返回空列表 []。
    """
    # str.replace：先把 Windows 换行 \\r\\n 统一成 \\n，避免段尾残留 \\r
    normalized = text.replace("\r\n", "\n")
    # str.split：按换行拆成行的列表
    lines = normalized.split("\n")

    # 判断全文是否存在空行（含空白字符的行也算空行）
    has_blank_line = any(line.strip() == "" for line in lines)

    # 全文无空行：按换行分段，一行一段，跳过可能的空白行
    if not has_blank_line:
        return [line.strip() for line in lines if line.strip() != ""]

    # 有空行：以空行为分隔符分组，一组为一段
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":  # 空行 = 段落分隔符
            if current:
                # str.join：把当前组内的行用换行拼起来作为一段
                # str.strip：去掉段落首尾的空白字符
                paragraphs.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    # 收尾：最后一段如果没有遇到空行，也要加进去
    if current:
        paragraphs.append("\n".join(current).strip())

    return paragraphs
