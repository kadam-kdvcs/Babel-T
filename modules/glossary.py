# -*- coding: utf-8 -*-
"""阿语翻译审校术语库核心模块（阶段 1）。

模块作用
--------
提供三个纯函数，供阿语翻译审校 MVP 第 1 阶段使用：

- normalize_arabic(text)：对阿拉伯语文本做归一化（去变音符等），
  使「带变音符的正文」与「不带变音符的词条」在归一化后可以互相命中；
- load_glossary(path)：读取术语库 terms.csv（六列）或专名库 proper_names.csv
  （四列）为 list[dict]，dict 的键 = CSV 原表头名；
- scan_glossary(paragraphs, entries)：在段落中统计每个词条的出现次数，
  输出命中报告（每条含出现段落列表与全文累计次数）。

归一化规则
----------
1. 删除全部阿拉伯语变音符（ً-ٕ 及补充变音符 ٰ）；
2. 删除延长符 tatweel（ـ ـ）；
3. 删除双向控制符（LRM/RLM/ALM 与嵌入/覆盖控制符 ‎-‮、؜），
   避免不可见字符干扰匹配；
4. 统一 alef 开头变体：أ إ آ ٱ → ا（词条与正文中这几种写法经常混用，
   折叠后即可互相命中）。

刻意不做（阶段 1 取舍）
----------------------
- 不做 ى→ي、ة→ه 折叠：这两个折叠误配率高（如 "سنة"（年）与 "سني"），
  宁可漏配也不误配，留待后续阶段引入上下文规则；
- 不处理 ؤ/ئ/ء：中位 hamza 是词义的一部分（مؤتمر 会议、رئيس 总统），
  直接删除会破坏词义；
- 不做词边界判断：扫描是纯子串匹配，词条 "فلسطين" 会命中 "الفلسطينية"，
  这是本阶段已知且接受的取舍（详见 scan_glossary docstring）。

设计取舍
--------
- 纯函数模块：无全局可变状态、无 UI、无 IO 依赖，文件路径由调用方传入；
- 归一化用一次 str.translate 完成：全部删除项与替换项合并为一张静态
  翻译表 _TRANSLATION_TABLE，避免逐字符循环，逻辑集中、性能好；
- 两库表头不同（六列 vs 四列），加载后直接以「CSV 原表头名」作为 dict 键，
  不硬编码字段名集合，因此一个加载函数可同时服务两个库；
- load_glossary 不捕获 IO 错误（如文件不存在），交由调用方统一处理。
"""

import csv
from pathlib import Path

# ---------------------------------------------------------------------------
# 归一化常量与翻译表
# ---------------------------------------------------------------------------

# 阿拉伯语变音符：ً-ْ 为标准变音符（上/下音符、叠音符 shadda、
# 静音符），ٓ-ٕ 为带 madda/hamza 的补充音符，ٰ 为 superscript
# alef（部分拼写系统使用）。这些符号只标读音、不改词形，全部删除。
_DIACRITICS = "ًٌٍَُِّْٰٕٓٔ"

# 延长符 tatweel（ـ ـ）：仅用于拉长字形、无语义，删除后不影响匹配。
_TATWEEL = "ـ"

# 双向控制符：‎ 左到右标记（LRM）、‏ 右到左标记（RLM）、
# ‪-‮ 双向嵌入/覆盖控制符、؜ 阿拉伯字母标记（ALM）。
# 这些不可见字符常混入复制粘贴的文本，会破坏子串匹配，一律删除。
_BIDI_CONTROLS = "‎‏‪‫‬‭‮؜"

# 字母开头变体 → 标准 alef（ا ا）：
# أ（上 hamza）、إ（下 hamza）、آ（madda）、ٱ（alef wasla）在词条与正文中
# 经常混用，统一折叠为 ا 可以提高互配率。
_LETTER_VARIANTS = {
    "أ": "ا",  # أ → ا
    "إ": "ا",  # إ → ا
    "آ": "ا",  # آ → ا
    "ٱ": "ا",  # ٱ → ا
}

# 删除集：把三个删除集合（变音符 + 延长符 + 双向控制符）的每个字符
# 映射到 None。str.maketrans 单参 dict 形式中，值为 None 表示「删除该字符」。
_DELETIONS = {ch: None for ch in _DIACRITICS + _TATWEEL + _BIDI_CONTROLS}

# str.maketrans(dict)：按 dict 生成翻译表——键为单个字符，值为单个字符（替换）
# 或 None（删除）。用 dict 合并运算符（|）把删除集与替换集（1 对 1）合成一张
# 总表，使 normalize_arabic 只需一次 translate 调用即可完成全部归一化。
_TRANSLATION_TABLE = str.maketrans(_DELETIONS | _LETTER_VARIANTS)


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

def normalize_arabic(text: str) -> str:
    """归一化阿拉伯语文本。

    作用
    ----
    删除变音符、延长符（tatweel）与双向控制符，并把 أ إ آ ٱ 统一折叠为 ا，
    使「带变音符的正文」与「不带变音符的词条」在归一化后可以互相命中。

    输入
    ----
    text : str —— 待归一化的文本（可为空字符串）。

    输出
    ----
    str —— 归一化后的文本；输入空串时返回空串。

    刻意不做（理由见模块 docstring）
    --------------------------------
    - 不做 ى→ي、ة→ه 折叠：防止误配（如 "سنة"（年）与 "سني"）；
    - 不处理 ؤ/ئ/ء：中位 hamza 是词义一部分（مؤتمر 会议、رئيس 总统）；
    - 不做词边界判断（边界规则属于扫描阶段，见 scan_glossary）。
    """
    # str.translate(table)：按 table 逐字符替换；值为 None 的项对应删除。
    # 归一化只依赖模块级静态翻译表，无状态、可并发调用。
    return text.translate(_TRANSLATION_TABLE)


# ---------------------------------------------------------------------------
# 加载词条库
# ---------------------------------------------------------------------------

def load_glossary(path: str | Path) -> list[dict]:
    """从 CSV 加载术语库或专名库。

    作用
    ----
    读取 terms.csv（六列：阿语原文,中文译文,类别,领域,备注,处理方式）
    或 proper_names.csv（四列：阿语原文,中文译文,类别,备注），
    返回每行一个 dict 的列表。

    输入
    ----
    path : str | pathlib.Path —— CSV 文件路径。

    输出
    ----
    list[dict] —— 每行一个 dict，键 = CSV 原表头名（两个库表头不同，
        因此不硬编码列数）；键与值均已 strip；「阿语原文」为空的整行跳过；
        文件只有表头或为空 → 返回空列表 []；
        文件不存在 → 抛出 FileNotFoundError（本函数不捕获，由调用方处理）。
    """
    # open(path, "r", encoding="utf-8-sig", newline="")：
    #   encoding="utf-8-sig" 自动剥离 Excel 保存时写在文件头的 BOM（﻿），
    #   避免出现 "﻿阿语原文" 这类带前缀的表头键；
    #   newline="" 在 Windows 下禁用换行符自动翻译，防止字段末尾混入 \r。
    # 文件不存在时 open 自然抛出 FileNotFoundError，此处刻意不 try。
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        # csv.DictReader(f)：按首行表头为每一行生成 dict——
        # 键 = 表头单元格文本，值 = 该列单元格文本。
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            # raw 为 OrderedDict。这里对键和值都做 strip，去掉手工编辑残留的
            # 首尾空格。注意：行内比表头多出的列（如行尾多一个逗号）会被
            # DictReader 放进 key=None 下，它不属于表头字段，过滤掉以免
            # None.strip() 抛 AttributeError。
            cleaned = {
                k.strip(): (v.strip() if v is not None else "")
                for k, v in raw.items()
                if k is not None
            }
            # 「阿语原文」为空的行跳过（防止手工编辑留下的空行进入词条库）。
            if not cleaned.get("阿语原文", ""):
                continue
            rows.append(cleaned)
        return rows


# ---------------------------------------------------------------------------
# 扫描词条
# ---------------------------------------------------------------------------

def scan_glossary(paragraphs: list[str], entries: list[dict]) -> list[dict]:
    """在段落中扫描词条并按词条聚合统计出现次数。

    作用
    ----
    对每个词条 × 每个段落做归一化后的纯子串匹配，统计每个词条在全文的
    出现次数与出现段落，返回命中报告（按首次出现段落号升序）。

    输入
    ----
    paragraphs : list[str] —— 待扫描的段落，按文档先后顺序排列（可为空）；
    entries    : list[dict] —— 词条列表（load_glossary 的输出），
        每个 dict 含「阿语原文」等字段，键 = CSV 表头名（可为空）。

    输出
    ----
    list[dict] —— 命中报告，按「首次出现段落号」升序排列。每条命中为原词条
        全部字段的拷贝（键 = CSV 表头名）外加两个新字段：
            "paragraphs" : list[int] —— 出现该词条的段落号列表（1 基，升序）；
            "count"      : int —— 该词条在全文的累计出现次数（≥ 1）。
        同一词条跨多段出现只产出一条命中：段落号并入 paragraphs 列表、
        次数累计进 count，不按段落拆行（避免表格出现重复词条行）。
        paragraphs 为空、entries 为空或没有任何命中 → 返回 []。

    已知取舍（阶段 1）
    ------------------
    - 纯子串匹配、无词边界判断：词条 "فلسطين" 会命中正文 "الفلسطينية"
      （派生词），这是本阶段预期行为，后续阶段再引入边界规则；
    - 匹配前双方都做归一化：正文 "نؤكد على السَّلام" 可命中词条 "السلام"
      （正文的变音符被删除后即可命中）。
    """
    # 先把每个词条归一化一次，供所有段落复用，避免对同一词条重复计算。
    # 命中报告仍保留原词条字段（未归一化），键 = CSV 表头名。
    norm_terms = []
    for entry in entries:
        # entry.get("阿语原文", "")：两个库都以「阿语原文」为词条字段；
        # 缺该字段的脏数据按空串处理，交给下方空串过滤统一跳过。
        term = normalize_arabic(entry.get("阿语原文", ""))
        # 词条归一化后为空串（例如原文只剩变音符）则不参与匹配。
        if term:
            norm_terms.append((entry, term))

    # 每个段落也只归一化一次，供该段所有词条复用。
    norm_paragraphs = [normalize_arabic(p) for p in paragraphs]

    hits = []
    # 外层按词条聚合：一个词条跨多段出现只产出一条命中
    for entry, term in norm_terms:
        para_list: list[int] = []  # 出现该词条的段落号（1 基）
        total = 0                  # 全文累计出现次数
        # enumerate(..., start=1)：段落号采用 1 基编号
        for para_no, norm_para in enumerate(norm_paragraphs, start=1):
            # str.find(sub, start)：从下标 start 起查找子串 sub，返回首次
            # 出现的下标，找不到返回 -1。循环用 pos+1 作为下次查找起点，
            # 直到返回 -1，即可数出该段内的全部出现次数。
            count = 0
            pos = norm_para.find(term)
            while pos != -1:
                count += 1
                pos = norm_para.find(term, pos + 1)
            if count > 0:
                # 该段有命中：记录段落号，并把该段次数累计进总数
                para_list.append(para_no)
                total += count
        if total > 0:
            # dict(entry)：浅拷贝原词条全部字段（键 = CSV 表头名），
            # 不修改调用方传入的词条，保持纯函数无副作用。
            hit = dict(entry)
            hit["paragraphs"] = para_list  # 出现段落号列表（升序）
            hit["count"] = total           # 全文累计出现次数
            hits.append(hit)

    # list.sort(key=...)：按首次出现段落号升序排列；Python 的排序稳定，
    # 首次出现段落相同的多条命中保持词条库原有的先后顺序。
    hits.sort(key=lambda hit: hit["paragraphs"][0])
    return hits
