"""审校记录持久化模块（第 1 阶段占位，不实现任何功能）。

模块作用：负责把每次审校记录（原文、译文、术语/专名命中、审校报告等）
保存下来，供后续查询与追溯。第 1 阶段仅保留本模块作为占位，
不实现任何功能，也不参与流水线调用。

规划（阶段 4）：
- 使用 SQLite 保存每次审校记录，表结构示意如下：
    reviews(
        id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- 记录主键
        created_at   TEXT,    -- 创建时间
        source_text  TEXT,    -- 阿语原文
        translations TEXT,    -- 译文（JSON 序列化后存储）
        term_hits    TEXT,    -- 术语命中（JSON 序列化后存储）
        report       TEXT     -- 审校报告（markdown 文本）
    )
- 未来接口规划：
    save_review(...) -> int   —— 保存一条审校记录，返回新记录的 id；
    list_reviews(...)         —— 按条件查询历史审校记录列表。

TODO（阶段 4）：编写 SQLite 连接管理、建表脚本与上述两个函数，
并把它们接入主流水线（保存成功后可在页面上回显历史记录）。

注意：第 1 阶段不导入本模块。
"""
