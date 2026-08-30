# -*- coding: utf-8 -*-
"""SQLite 存储层测试（阶段 4）。

所有测试使用 pytest tmp_path 创建的临时数据库，不访问真实数据库，
不打真实 API。
"""

import sqlite3

import pytest

from modules import storage


def _db(tmp_path):
    """获取临时数据库路径。"""
    return tmp_path / "test.db"


def test_init_db_creates_tables(tmp_path):
    """验证数据库自动初始化：所有表都能查到。"""
    db = _db(tmp_path)
    storage.init_db(db)
    with storage._connect(db) as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"documents", "paragraphs", "translation_runs", "paragraph_results", "review_notes"} <= names


def test_create_document_and_save_paragraphs(tmp_path):
    """验证文档创建与段落顺序保存。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "测试文档", "第一段\n\n第二段")
    ids = storage.save_paragraphs(db, doc_id, ["第一段", "第二段"])
    assert len(ids) == 2
    details = storage.get_run_details(db, 1)  # 没有 run 时查不到，先只验证段落读取
    # 用直接查询验证段落顺序
    with storage._connect(db) as conn:
        rows = conn.execute(
            "SELECT paragraph_index, source_text FROM paragraphs WHERE document_id=? ORDER BY paragraph_index",
            (doc_id,),
        ).fetchall()
    assert [r["paragraph_index"] for r in rows] == [0, 1]
    assert [r["source_text"] for r in rows] == ["第一段", "第二段"]


def test_paragraph_index_unique_per_document(tmp_path):
    """验证同一文档段落编号不能重复（UNIQUE 约束存在且生效）。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "abc")
    storage.save_paragraphs(db, doc_id, ["a", "b"])
    # 直接插入同 document_id + paragraph_index 应触发 UNIQUE 约束
    with pytest.raises(sqlite3.IntegrityError):
        with storage._connect(db) as conn:
            conn.execute(
                "INSERT INTO paragraphs (document_id, paragraph_index, source_text)"
                " VALUES (?, 0, 'dup')",
                (doc_id,),
            )


def test_full_run_and_details(tmp_path):
    """验证一次完整运行：文档、运行、段落结果、详情查询。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "完整", "أبجد\nهوز")
    para_ids = storage.save_paragraphs(db, doc_id, ["أبجد", "هوز"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    storage.save_paragraph_result(db, run_id, para_ids[0], api_translation="译一")
    storage.save_paragraph_result(
        db, run_id, para_ids[1], api_translation="译二",
        llm_direct_translation="直译二", llm_corrected_translation="修二",
        llm_final_translation="终二",
    )
    storage.update_run_status(db, run_id, "completed", tradeoff_notes="取舍", report="报告", completed=True)
    details = storage.get_run_details(db, run_id)
    assert details["run"]["status"] == "completed"
    assert details["document"]["raw_text"] == "أبجد\nهوز"
    assert len(details["paragraphs"]) == 2
    assert details["paragraphs"][0]["api_translation"] == "译一"
    assert details["paragraphs"][1]["llm_final_translation"] == "终二"
    assert details["run"]["tradeoff_notes"] == "取舍"
    assert details["run"]["report"] == "报告"


def test_partial_failure_preserves_completed_results(tmp_path):
    """验证部分失败时，已完成段落结果仍可读取。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "partial", "a\nb")
    para_ids = storage.save_paragraphs(db, doc_id, ["a", "b"])
    run_id = storage.create_translation_run(db, doc_id, "api", "api")
    storage.save_paragraph_result(db, run_id, para_ids[0], api_translation="成功一", status="completed")
    storage.save_paragraph_result(db, run_id, para_ids[1], status="failed", error_message="失败二")
    storage.update_run_status(db, run_id, "partial", error_message="第2段翻译失败")
    details = storage.get_run_details(db, run_id)
    assert details["paragraphs"][0]["api_translation"] == "成功一"
    assert details["paragraphs"][1]["result_status"] == "failed"
    assert details["run"]["status"] == "partial"
    assert "第2段翻译失败" in details["run"]["error_message"]


def test_human_translation_and_review_notes(tmp_path):
    """验证人工译文和全文/分段审校意见保存。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "人工", "原文")
    para_num = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    storage.save_human_translation(db, run_id, para_num[0], "人工最终译文")
    note_id1 = storage.save_review_note(db, run_id, "全文意见", note_type="style", author="老师")
    note_id2 = storage.save_review_note(db, run_id, "分段意见", note_type="translation_error", paragraph_id=para_num[0])
    details = storage.get_run_details(db, run_id)
    assert details["paragraphs"][0]["human_final_translation"] == "人工最终译文"
    assert details["paragraphs"][0]["selected_source"] == "human"
    assert len(details["notes"]) == 2
    assert details["notes"][0]["paragraph_id"] is None
    assert details["notes"][1]["paragraph_id"] == para_num[0]


def test_list_runs_returns_recent_first(tmp_path):
    """验证历史任务查询。"""
    db = _db(tmp_path)
    for i in range(3):
        doc_id = storage.create_document(db, f"doc{i}", "text")
        storage.create_translation_run(db, doc_id, "mock", "mock")
    runs = storage.list_runs(db)
    assert len(runs) == 3
    assert runs[0]["id"] > runs[1]["id"] > runs[2]["id"]


def test_persistence_after_reconnect(tmp_path):
    """验证重启（重新连接）后数据仍存在。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "persist", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    storage.save_paragraph_result(db, run_id, para_ids[0], api_translation="译文")
    storage.update_run_status(db, run_id, "completed")
    # 重新打开新连接读取
    details = storage.get_run_details(db, run_id)
    assert details is not None
    assert details["paragraphs"][0]["api_translation"] == "译文"


def test_sql_injection_quotes_newlines_arabic(tmp_path):
    """验证用户输入含引号/换行/阿语/中文时不会破坏 SQL。"""
    db = _db(tmp_path)
    text = "'; DROP TABLE documents;--\n中文\nنص عربي\n\"quoted\""
    doc_id = storage.create_document(db, "注入测试", text)
    para_ids = storage.save_paragraphs(db, doc_id, [line for line in text.split("\n") if line])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    storage.save_paragraph_result(db, run_id, para_ids[0], api_translation=text)
    note_id = storage.save_review_note(db, run_id, text, note_type="other")
    details = storage.get_run_details(db, run_id)
    assert details["paragraphs"][0]["source_text"].startswith("'; DROP")
    assert details["paragraphs"][0]["api_translation"] == text
    assert details["notes"][0]["content"] == text
    # 确认表仍存在
    with storage._connect(db) as conn:
        assert conn.execute("SELECT NULL FROM documents LIMIT 1").fetchone() is not None


def test_different_runs_not_mixed(tmp_path):
    """验证不同运行之间的结果不串。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "text")
    para_ids = storage.save_paragraphs(db, doc_id, ["text"])
    run1 = storage.create_translation_run(db, doc_id, "mock", "mock")
    run2 = storage.create_translation_run(db, doc_id, "mock", "mock")
    storage.save_paragraph_result(db, run1, para_ids[0], api_translation="run1")
    storage.save_paragraph_result(db, run2, para_ids[0], api_translation="run2")
    d1 = storage.get_run_details(db, run1)
    d2 = storage.get_run_details(db, run2)
    assert d1["paragraphs"][0]["api_translation"] == "run1"
    assert d2["paragraphs"][0]["api_translation"] == "run2"


def test_empty_document_and_paragraphs(tmp_path):
    """验证空段落列表处理。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "empty", "")
    ids = storage.save_paragraphs(db, doc_id, [])
    assert ids == []
    with storage._connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0]
    assert count == 0


def test_db_path_unwritable_raises_clear_error(tmp_path):
    """验证数据库路径不可写时给出清晰错误。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    bad_path = blocker / "sub" / "x.db"
    with pytest.raises(sqlite3.Error):
        storage.init_db(bad_path)


def test_basic_concurrent_writes_no_corruption(tmp_path):
    """基本并发写入不会造成数据损坏（简单线程测试）。"""
    import threading

    db = _db(tmp_path)
    doc_id = storage.create_document(db, "concurrent", "text")
    para_ids = storage.save_paragraphs(db, doc_id, ["text"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    errors = []

    def worker(idx):
        try:
            storage.save_paragraph_result(db, run_id, para_ids[0], api_translation=f"v{idx}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    details = storage.get_run_details(db, run_id)
    assert details["paragraphs"][0]["api_translation"] in {f"v{i}" for i in range(5)}


# ---------------------------------------------------------------------------
# 教师审校记录（L1/L2/L3 + verified + 版本）
# ---------------------------------------------------------------------------

def test_create_and_save_l1_review(tmp_path):
    """验证教师 L1 可创建保存，draft_translation 不会被覆盖。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(
        db, run_id, para_ids[0], "原文", "AI原译文"
    )
    storage.save_l1_review(
        db, rec_id,
        teacher_decision="revise",
        teacher_error_types=["omission", "other"],
        teacher_custom_error_types="术语库更新建议",
        teacher_severity="moderate",
        teacher_revision="教师修改稿",
        teacher_raw_comment="漏译了一个词",
    )
    rec = storage.get_review_record(db, rec_id)
    assert rec["draft_translation"] == "AI原译文"
    assert rec["teacher_revision"] == "教师修改稿"
    assert rec["teacher_raw_comment"] == "漏译了一个词"
    assert rec["teacher_error_types"] == ["omission", "other"]
    assert rec["teacher_custom_error_types"] == "术语库更新建议"


def test_l2_save_does_not_overwrite_l1(tmp_path):
    """验证 L2 保存不会覆盖教师 L1。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI原译文")
    storage.save_l1_review(db, rec_id, teacher_decision="revise",
                           teacher_error_types=["style"], teacher_severity="minor",
                           teacher_revision="改稿", teacher_raw_comment="原话")
    storage.save_l2_review(db, rec_id, llm_raw_normalized_response="raw",
                           normalized_review={"decision": "revise"},
                           llm_model="deepseek-chat", prompt_version="v1",
                           normalized_status="success")
    rec = storage.get_review_record(db, rec_id)
    assert rec["teacher_raw_comment"] == "原话"
    assert rec["teacher_revision"] == "改稿"
    assert rec["llm_model"] == "deepseek-chat"
    assert rec["normalized_review"]["decision"] == "revise"


def test_l1_save_without_technical_fields_saves_diff(tmp_path):
    """验证新简化 L1：不要求问题类型/严重程度，自动保存修改差异。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI原译文")

    storage.save_l1_review(
        db, rec_id,
        teacher_decision="revise",
        teacher_revision="教师修改稿",
        teacher_raw_comment="漏译了一个词",
    )

    rec = storage.get_review_record(db, rec_id)
    assert rec["draft_translation"] == "AI原译文"
    assert rec["teacher_revision"] == "教师修改稿"
    assert rec["teacher_raw_comment"] == "漏译了一个词"
    assert rec["teacher_error_types"] == []
    assert rec["teacher_severity"] == "null"
    assert "AI 原始译文：AI原译文" in (rec["translation_diff"] or "")
    assert "教师最终译文：教师修改稿" in (rec["translation_diff"] or "")


def test_l1_save_pass_without_change_has_no_diff(tmp_path):
    """验证“通过且未修改”时 translation_diff 为空。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI原译文")

    storage.save_l1_review(
        db, rec_id,
        teacher_decision="pass",
        teacher_revision="AI原译文",
        teacher_raw_comment="",
    )

    rec = storage.get_review_record(db, rec_id)
    assert rec["teacher_revision"] == "AI原译文"
    assert rec["translation_diff"] == ""


def test_retranslate_l1_preserves_draft_and_raw_comment(tmp_path):
    """验证重译结论仍保留 AI 原始译文与教师原始说明。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI原译文")

    storage.save_l1_review(
        db, rec_id,
        teacher_decision="retranslate",
        teacher_revision="",
        teacher_raw_comment="请整段重译，目前意思完全不对",
    )

    rec = storage.get_review_record(db, rec_id)
    assert rec["draft_translation"] == "AI原译文"
    assert rec["teacher_decision"] == "retranslate"
    assert rec["teacher_raw_comment"] == "请整段重译，目前意思完全不对"


def test_missing_l2_does_not_auto_verified(tmp_path):
    """验证 L2 失败/未生成时，记录保持未确认且不可进入 verified。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI")

    storage.save_l1_review(
        db, rec_id,
        teacher_decision="revise",
        teacher_revision="教师改",
        teacher_raw_comment="AI 标准化未调用",
    )
    rec = storage.get_review_record(db, rec_id)
    assert rec["normalized_review"] == {}
    assert rec["normalized_status"] is None
    assert rec["verified"] is False


def test_l3_verified_and_not_auto_verified(tmp_path):
    """验证只有教师确认后才 verified=true。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    rec_id = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI")
    storage.save_l1_review(db, rec_id, teacher_decision="pass",
                           teacher_error_types=[], teacher_severity="null",
                           teacher_revision="AI", teacher_raw_comment="")
    # L1 保存后未确认，不应 verified
    rec = storage.get_review_record(db, rec_id)
    assert rec["verified"] is False
    storage.save_l3_verified(db, rec_id, verified_review={"decision": "pass"}, reviewer_id="teacher")
    rec = storage.get_review_record(db, rec_id)
    assert rec["verified"] is True
    assert rec["verified_review"]["decision"] == "pass"


def test_review_record_versioning(tmp_path):
    """验证同一单元多次审校产生新版本，不覆盖旧版本。"""
    db = _db(tmp_path)
    doc_id = storage.create_document(db, "doc", "原文")
    para_ids = storage.save_paragraphs(db, doc_id, ["原文"])
    run_id = storage.create_translation_run(db, doc_id, "mock", "mock")
    r1 = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI")
    storage.save_l1_review(db, r1, teacher_decision="revise",
                           teacher_error_types=["omission"], teacher_severity="minor",
                           teacher_revision="v1", teacher_raw_comment="第一版")
    r2 = storage.create_review_record(db, run_id, para_ids[0], "原文", "AI")
    storage.save_l1_review(db, r2, teacher_decision="pass",
                           teacher_error_types=[], teacher_severity="null",
                           teacher_revision="v2", teacher_raw_comment="第二版")
    latest = storage.get_latest_review_record(db, run_id, para_ids[0])
    assert latest["revision"] == 2
    old = storage.get_review_record(db, r1)
    assert old["teacher_raw_comment"] == "第一版"
    assert old["revision"] == 1
