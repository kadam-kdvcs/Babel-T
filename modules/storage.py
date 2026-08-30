# -*- coding: utf-8 -*-
"""SQLite 存储层（阶段 4）。

模块作用
--------
使用 Python 标准库 sqlite3，为翻译审校任务提供持久化：
- 文档与段落
- 翻译运行（translation_run）
- 每段四种译文 / 人工译文 / 来源选择
- 全文与分段审校意见

设计原则
--------
- 不使用 ORM，只用标准库 sqlite3；
- 所有 SQL 使用参数化，禁止拼接用户输入；
- 所有写操作在事务中完成；
- 外键约束开启；
- 数据库路径默认 data/translations.db，可通过环境变量 DATABASE_PATH 覆盖；
- 不保存 API Key / Secret；
- 模块不依赖 Streamlit。

数据库路径约定
--------------
- 默认：项目根目录 / data / translations.db；
- 可通过环境变量 DATABASE_PATH 指定绝对或相对路径；
- 调用方也可以显式传入 db_path 参数，测试必须使用临时数据库。
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "translations.db"
ENV_DB_PATH = "DATABASE_PATH"

# 运行状态
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# 段落结果状态
PARA_STATUS_PENDING = "pending"
PARA_STATUS_COMPLETED = "completed"
PARA_STATUS_PARTIAL = "partial"
PARA_STATUS_FAILED = "failed"

# 可选来源
SELECTED_SOURCE_API = "api"
SELECTED_SOURCE_LLM_DIRECT = "llm_direct"
SELECTED_SOURCE_LLM_CORRECTED = "llm_corrected"
SELECTED_SOURCE_LLM_FINAL = "llm_final"
SELECTED_SOURCE_HUMAN = "human"


def _get_db_path(db_path: str | Path | None) -> Path:
    """返回实际使用的数据库路径。

    作用：优先使用调用方传入的 db_path；否则读取环境变量 DATABASE_PATH；
          仍未设置时使用项目默认 data/translations.db。
    输入：db_path —— 可选的显式路径。
    输出：Path —— 数据库文件绝对或相对路径。
    """
    if db_path is not None:
        return Path(db_path)
    env_path = os.environ.get(ENV_DB_PATH, "")
    if env_path.strip():
        return Path(env_path.strip())
    return DEFAULT_DB_PATH


@contextmanager
def _connect(db_path: str | Path | None = None):
    """打开 SQLite 连接，确保外键开启、异常时回滚、结束时关闭。

    作用：统一管理连接生命周期。每个函数都通过这里的上下文管理器获取
          连接，避免连接泄漏。
    输入：db_path —— 数据库路径；None 表示用默认路径。
    输出：yield sqlite3.Connection。
    """
    path = _get_db_path(db_path)
    # 如果数据库文件不存在且父目录不存在，尝试创建父目录（默认 data/）
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    except (OSError, sqlite3.Error) as e:
        raise sqlite3.Error(f"无法打开数据库 {path}：{e}") from e
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_lang TEXT NOT NULL DEFAULT 'ar',
    target_lang TEXT NOT NULL DEFAULT 'zh',
    raw_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    paragraph_index INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    UNIQUE(document_id, paragraph_index)
);

CREATE TABLE IF NOT EXISTS translation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running',
    translation_engine TEXT NOT NULL,
    review_engine TEXT NOT NULL,
    translation_model TEXT,
    review_model TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT,
    tradeoff_notes TEXT,
    report TEXT
);

CREATE TABLE IF NOT EXISTS paragraph_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES translation_runs(id) ON DELETE CASCADE,
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
    api_translation TEXT,
    llm_direct_translation TEXT,
    llm_corrected_translation TEXT,
    llm_final_translation TEXT,
    human_final_translation TEXT,
    selected_source TEXT NOT NULL DEFAULT 'llm_final',
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    UNIQUE(run_id, paragraph_id)
);

CREATE TABLE IF NOT EXISTS review_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES translation_runs(id) ON DELETE CASCADE,
    paragraph_id INTEGER REFERENCES paragraphs(id) ON DELETE CASCADE,
    note_type TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES translation_runs(id) ON DELETE CASCADE,
    unit_id INTEGER NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1,

    source_text TEXT NOT NULL,
    draft_translation TEXT,

    teacher_decision TEXT,
    teacher_error_types TEXT,
    teacher_custom_error_types TEXT,
    teacher_severity TEXT,
    teacher_revision TEXT,
    teacher_raw_comment TEXT,

    translation_diff TEXT,

    llm_raw_normalized_response TEXT,
    normalized_review TEXT,
    llm_model TEXT,
    prompt_version TEXT,
    normalized_at TEXT,
    normalized_status TEXT,

    verified_review TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    reviewer_id TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, unit_id, revision)
);
"""


def init_db(db_path: str | Path | None = None) -> None:
    """初始化数据库：创建所有表。

    作用：数据库不存在时自动创建表；已存在则只执行 IF NOT EXISTS。
    输入：db_path —— 可选的数据库路径。
    输出：无。
    异常：路径不可写时抛出 sqlite3.Error（带中文说明）。
    """
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 非破坏式迁移：为已存在的 review_records 补充其他问题类型字段
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(review_records)").fetchall()}
        if "teacher_custom_error_types" not in cols:
            conn.execute("ALTER TABLE review_records ADD COLUMN teacher_custom_error_types TEXT")


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_document(
    db_path: str | Path | None,
    title: str,
    raw_text: str,
    source_lang: str = "ar",
    target_lang: str = "zh",
) -> int:
    """创建一个文档记录，返回 document_id。

    输入：title —— 标题；raw_text —— 完整原文；
          source_lang / target_lang —— 语言代码。
    输出：int —— 文档 id。
    """
    init_db(db_path)
    ts = now_iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO documents (title, source_lang, target_lang, raw_text, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, source_lang, target_lang, raw_text, ts, ts),
        )
        return int(cur.lastrowid)


def save_paragraphs(
    db_path: str | Path | None,
    document_id: int,
    paragraphs: list[str],
) -> list[int]:
    """保存文档的段落列表，返回段落 id 列表（按传入顺序）。

    作用：把一次用户提交的文本按段落分批写入 paragraphs 表。
    输入：document_id —— 文档 id；paragraphs —— 段落字符串列表。
    输出：list[int] —— 与段落顺序一致的 paragraph_id 列表。
    """
    init_db(db_path)
    ids: list[int] = []
    with _connect(db_path) as conn:
        for index, text in enumerate(paragraphs):
            cur = conn.execute(
                "INSERT OR IGNORE INTO paragraphs (document_id, paragraph_index, source_text)"
                " VALUES (?, ?, ?)",
                (document_id, index, text),
            )
            if cur.lastrowid is None:
                # OR IGNORE 不会设置 lastrowid，需要读回已有 id
                row = conn.execute(
                    "SELECT id FROM paragraphs WHERE document_id = ? AND paragraph_index = ?",
                    (document_id, index),
                ).fetchone()
                ids.append(int(row["id"]))
            else:
                ids.append(int(cur.lastrowid))
    return ids


def create_translation_run(
    db_path: str | Path | None,
    document_id: int,
    translation_engine: str,
    review_engine: str,
    translation_model: str = "",
    review_model: str = "",
    status: str = STATUS_RUNNING,
) -> int:
    """创建一个翻译运行记录，返回 run_id。

    输入：document_id —— 文档 id；translation_engine / review_engine ——
          mock 或 api；model 可为空；status —— 初始状态。
    输出：int —— 运行 id。
    """
    init_db(db_path)
    ts = now_iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO translation_runs"
            " (document_id, status, translation_engine, review_engine,"
            "  translation_model, review_model, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                status,
                translation_engine,
                review_engine,
                translation_model,
                review_model,
                ts,
            ),
        )
        return int(cur.lastrowid)


def save_paragraph_result(
    db_path: str | Path | None,
    run_id: int,
    paragraph_id: int,
    *,
    api_translation: str | None = None,
    llm_direct_translation: str | None = None,
    llm_corrected_translation: str | None = None,
    llm_final_translation: str | None = None,
    human_final_translation: str | None = None,
    selected_source: str = SELECTED_SOURCE_LLM_FINAL,
    status: str = PARA_STATUS_COMPLETED,
    error_message: str | None = None,
) -> None:
    """保存或更新一个段落的单条结果。

    作用：按 (run_id, paragraph_id) 唯一约束，用 INSERT ... ON CONFLICT DO
          UPDATE 完成增量保存——每次只更新传入的非空字段，避免覆盖已保存
          的其他阶段结果。
    输入：run_id / paragraph_id —— 运行与段落；各译文；selected_source；
          status；error_message。
    输出：无。
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO paragraph_results
              (run_id, paragraph_id, api_translation, llm_direct_translation,
               llm_corrected_translation, llm_final_translation,
               human_final_translation, selected_source, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, paragraph_id) DO UPDATE SET
              api_translation = COALESCE(excluded.api_translation, paragraph_results.api_translation),
              llm_direct_translation = COALESCE(excluded.llm_direct_translation, paragraph_results.llm_direct_translation),
              llm_corrected_translation = COALESCE(excluded.llm_corrected_translation, paragraph_results.llm_corrected_translation),
              llm_final_translation = COALESCE(excluded.llm_final_translation, paragraph_results.llm_final_translation),
              human_final_translation = COALESCE(excluded.human_final_translation, paragraph_results.human_final_translation),
              selected_source = excluded.selected_source,
              status = excluded.status,
              error_message = COALESCE(excluded.error_message, paragraph_results.error_message)
            """,
            (
                run_id,
                paragraph_id,
                api_translation,
                llm_direct_translation,
                llm_corrected_translation,
                llm_final_translation,
                human_final_translation,
                selected_source,
                status,
                error_message,
            ),
        )


def save_paragraph_results(
    db_path: str | Path | None,
    run_id: int,
    results: list[dict],
) -> None:
    """批量保存多个段落结果。"""
    for item in results:
        save_paragraph_result(
            db_path,
            run_id,
            item["paragraph_id"],
            api_translation=item.get("api_translation"),
            llm_direct_translation=item.get("llm_direct_translation"),
            llm_corrected_translation=item.get("llm_corrected_translation"),
            llm_final_translation=item.get("llm_final_translation"),
            human_final_translation=item.get("human_final_translation"),
            selected_source=item.get("selected_source", SELECTED_SOURCE_LLM_FINAL),
            status=item.get("status", PARA_STATUS_COMPLETED),
            error_message=item.get("error_message"),
        )


def update_run_status(
    db_path: str | Path | None,
    run_id: int,
    status: str,
    *,
    error_message: str | None = None,
    tradeoff_notes: str | None = None,
    report: str | None = None,
    completed: bool = False,
) -> None:
    """更新运行状态，可附带错误信息、取舍说明、审校报告。

    输入：run_id；status；error_message；tradeoff_notes；report；completed
          是否写 completed_at。
    输出：无。
    """
    init_db(db_path)
    ts = now_iso()
    fields = ["status = ?"]
    params: list = [status]
    if error_message is not None:
        fields.append("error_message = ?")
        params.append(error_message)
    if tradeoff_notes is not None:
        fields.append("tradeoff_notes = ?")
        params.append(tradeoff_notes)
    if report is not None:
        fields.append("report = ?")
        params.append(report)
    if completed:
        fields.append("completed_at = ?")
        params.append(ts)
    params.append(run_id)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE translation_runs SET {', '.join(fields)} WHERE id = ?",
            params,
        )


def save_review_note(
    db_path: str | Path | None,
    run_id: int,
    content: str,
    note_type: str = "other",
    *,
    paragraph_id: int | None = None,
    author: str = "",
) -> int:
    """保存一条人工审校意见，返回 note_id。

    作用：paragraph_id 为 None 时表示全文意见；否则表示某段意见。
    输入：run_id；content；note_type；可选 paragraph_id / author。
    输出：int —— 意见 id。
    """
    init_db(db_path)
    ts = now_iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO review_notes (run_id, paragraph_id, note_type, content, author, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, paragraph_id, note_type, content, author, ts),
        )
        return int(cur.lastrowid)


def save_human_translation(
    db_path: str | Path | None,
    run_id: int,
    paragraph_id: int,
    human_final_translation: str,
) -> None:
    """保存人工最终译文。

    输入：run_id；paragraph_id；human_final_translation。
    输出：无。
    """
    save_paragraph_result(
        db_path,
        run_id,
        paragraph_id,
        human_final_translation=human_final_translation,
        selected_source=SELECTED_SOURCE_HUMAN,
        status=PARA_STATUS_COMPLETED,
    )


def get_run(db_path: str | Path | None, run_id: int) -> dict | None:
    """查询单个运行记录（不含段落与意见）。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM translation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def list_runs(db_path: str | Path | None, limit: int = 50) -> list[dict]:
    """按创建时间倒序返回运行记录列表（不含段落与意见）。"""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM translation_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_run_details(db_path: str | Path | None, run_id: int) -> dict | None:
    """查询一次运行的完整详情：运行、文档、段落结果、审校意见。

    输出：dict 或 None。段落结果按 paragraph_index 排序。
    """
    init_db(db_path)
    with _connect(db_path) as conn:
        run = conn.execute(
            "SELECT * FROM translation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            return None
        run_dict = dict(run)
        document = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (run_dict["document_id"],)
        ).fetchone()
        rows = conn.execute(
            """
            SELECT p.id AS paragraph_id, p.paragraph_index, p.source_text,
                   pr.api_translation, pr.llm_direct_translation,
                   pr.llm_corrected_translation, pr.llm_final_translation,
                   pr.human_final_translation, pr.selected_source,
                   pr.status AS result_status, pr.error_message AS result_error
            FROM paragraphs p
            LEFT JOIN paragraph_results pr ON pr.paragraph_id = p.id AND pr.run_id = ?
            WHERE p.document_id = ?
            ORDER BY p.paragraph_index
            """,
            (run_id, run_dict["document_id"]),
        ).fetchall()
        notes = conn.execute(
            "SELECT * FROM review_notes WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
        return {
            "run": run_dict,
            "document": dict(document) if document else None,
            "paragraphs": [dict(row) for row in rows],
            "notes": [dict(row) for row in notes],
        }


# ---------------------------------------------------------------------------
# 教师审校记录（阶段 4.1：L1 / L2 / L3 + verified）
# ---------------------------------------------------------------------------

def _next_revision(db_path: str | Path | None, run_id: int, unit_id: int) -> int:
    """返回某运行某翻译单元的下一个 revision 号。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 AS rev FROM review_records"
            " WHERE run_id = ? AND unit_id = ?",
            (run_id, unit_id),
        ).fetchone()
        return int(row["rev"])


def create_review_record(
    db_path: str | Path | None,
    run_id: int,
    unit_id: int,
    source_text: str,
    draft_translation: str,
    *,
    revision: int | None = None,
) -> int:
    """创建一个新的审校版本记录，返回 record_id。

    作用：每次教师提交 L1 时调用。若未显式指定 revision，则自动取
          max(revision)+1，保证同一单元多次审校不覆盖旧版本。
    """
    if revision is None:
        revision = _next_revision(db_path, run_id, unit_id)
    ts = now_iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO review_records
              (run_id, unit_id, revision, source_text, draft_translation,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, unit_id, revision, source_text, draft_translation, ts, ts),
        )
        return int(cur.lastrowid)


def save_l1_review(
    db_path: str | Path | None,
    record_id: int,
    *,
    teacher_decision: str,
    teacher_error_types: list[str] | None = None,
    teacher_custom_error_types: str = "",
    teacher_severity: str = "null",
    teacher_revision: str = "",
    teacher_raw_comment: str = "",
) -> None:
    """保存教师原始审校 L1。

    输入：
      - teacher_decision：pass / revise / retranslate（教师结论，必填）
      - teacher_error_types：可选问题类型列表；不传时保存空列表，
        问题类型由 L2 AI 标准化时推断，不要求教师手工填写。
      - teacher_custom_error_types：其他问题类型的文字归类（兼容旧数据）
      - teacher_severity：可选严重程度；不传时保存 "null"
      - teacher_revision：教师最终译文
      - teacher_raw_comment：教师原始说明（绝对原样保存）

    规则：
    - draft_translation 在创建记录时已固定，本函数绝不更新它；
    - teacher_raw_comment 只在这里写入，后续 L2/L3 不覆盖；
    - 自动根据 AI 原始译文与教师最终译文生成 translation_diff；
    - teacher_error_types / teacher_severity 只作为旧字段兼容保留，
      新界面不再要求教师填写，AI 推断结果写入 normalized_review。
    """
    ts = now_iso()
    error_types = list(teacher_error_types or [])
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT draft_translation FROM review_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        draft = (row["draft_translation"] or "") if row else ""
        revision = teacher_revision or ""
        if draft.strip() and revision.strip() and revision != draft:
            translation_diff = f"AI 原始译文：{draft}\n教师最终译文：{revision}"
        else:
            translation_diff = ""
        conn.execute(
            """
            UPDATE review_records SET
              teacher_decision = ?,
              teacher_error_types = ?,
              teacher_custom_error_types = ?,
              teacher_severity = ?,
              teacher_revision = ?,
              teacher_raw_comment = ?,
              translation_diff = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                teacher_decision,
                _json_dumps(error_types),
                teacher_custom_error_types,
                teacher_severity,
                revision,
                teacher_raw_comment,
                translation_diff,
                ts,
                record_id,
            ),
        )


def save_l2_review(
    db_path: str | Path | None,
    record_id: int,
    *,
    llm_raw_normalized_response: str,
    normalized_review: dict,
    llm_model: str,
    prompt_version: str,
    normalized_status: str,
) -> None:
    """保存 AI 标准化审校 L2。

    注意：只更新 L2 相关字段，不会覆盖 L1。
    """
    ts = now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE review_records SET
              llm_raw_normalized_response = ?,
              normalized_review = ?,
              llm_model = ?,
              prompt_version = ?,
              normalized_at = ?,
              normalized_status = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                llm_raw_normalized_response,
                _json_dumps(normalized_review),
                llm_model,
                prompt_version,
                ts,
                normalized_status,
                ts,
                record_id,
            ),
        )


def save_l3_verified(
    db_path: str | Path | None,
    record_id: int,
    *,
    verified_review: dict,
    reviewer_id: str,
) -> None:
    """保存教师确认后的 L3，并置 verified=true。

    只有在教师确认/修改后确认时调用。
    """
    ts = now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE review_records SET
              verified_review = ?,
              verified = 1,
              verified_at = ?,
              reviewer_id = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                _json_dumps(verified_review),
                ts,
                reviewer_id,
                ts,
                record_id,
            ),
        )


def get_review_record(db_path: str | Path | None, record_id: int) -> dict | None:
    """按 id 读取一条审校记录，并自动解析 JSON 字段。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM review_records WHERE id = ?", (record_id,)
        ).fetchone()
        return _row_to_review_dict(row) if row else None


def get_latest_review_record(
    db_path: str | Path | None, run_id: int, unit_id: int
) -> dict | None:
    """读取某翻译单元的最新审校版本。"""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM review_records WHERE run_id = ? AND unit_id = ?"
            " ORDER BY revision DESC, id DESC LIMIT 1",
            (run_id, unit_id),
        ).fetchone()
        return _row_to_review_dict(row) if row else None


def list_review_records(db_path: str | Path | None, run_id: int) -> list[dict]:
    """读取某次运行的全部审校记录（全部版本）。"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM review_records WHERE run_id = ? ORDER BY unit_id ASC, revision DESC",
            (run_id,),
        ).fetchall()
        return [_row_to_review_dict(row) for row in rows]


def _json_dumps(value) -> str:
    """把 list/dict 序列化为 JSON 字符串。"""
    import json
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None):
    """解析 JSON 字符串，失败时返回 None。"""
    import json
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _row_to_review_dict(row) -> dict:
    """把 sqlite Row 转成可读 dict，自动解析 JSON 字段。"""
    data = dict(row)
    data["teacher_error_types"] = _json_loads(data.get("teacher_error_types")) or []
    data["normalized_review"] = _json_loads(data.get("normalized_review")) or {}
    data["verified_review"] = _json_loads(data.get("verified_review")) or {}
    data["verified"] = bool(data.get("verified"))
    return data

