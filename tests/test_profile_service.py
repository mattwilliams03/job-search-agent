"""
Tests for src/core/profile_service.py.

LLM calls are always mocked (profile_service.llm.complete) - nothing here
makes a real network/API call. Two tests exercise the real markitdown
conversion path against hand-crafted PDF fixtures under tests/fixtures/;
everything else monkeypatches profile_service._convert_to_markdown
directly to control exact source text.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import config
from src.core import profile_service
from src.db import connection as db_connection
from src.db.migrate import migrate

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_V1 = FIXTURES_DIR / "sample_resume_v1.pdf"
SAMPLE_V2 = FIXTURES_DIR / "sample_resume_v2.pdf"


def _db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """
    A tmp_path-based SQLite DB with migrations already applied.
    Also points config.PROFILE_MD_PATH at a tmp_path location, since
    every successful (non-skipped) ingest_document/seed_profile call now
    auto-exports - without this, tests would write to the real project's
    profile/profile.md as a side effect.
    """
    path = tmp_path / "test.db"
    migrate(db_path=path)
    monkeypatch.setattr(config, "PROFILE_MD_PATH", tmp_path / "profile.md")
    return path


def _extract_response(facts, style_observations=None):
    return json.dumps({"facts": facts, "style_observations": style_observations or []})


def _merge_response(decisions):
    return json.dumps({"decisions": decisions})


def _mock_llm(monkeypatch, *, extract=None, merge=None, summarize_history=None):
    """
    Install a mock for profile_service.llm.complete that dispatches on
    the `task` kwarg. Each of extract/merge/summarize_history may be a
    single return value or a list (consumed in order across multiple
    calls for that task).
    """
    extract_queue = list(extract) if isinstance(extract, list) else ([extract] if extract is not None else [])
    merge_queue = list(merge) if isinstance(merge, list) else ([merge] if merge is not None else [])
    history_queue = (
        list(summarize_history) if isinstance(summarize_history, list)
        else ([summarize_history] if summarize_history is not None else [])
    )

    def fake_complete(*, task, system, user, max_tokens=4096, model=None):
        if task == "extract_facts":
            return extract_queue.pop(0)
        if task == "merge_facts":
            return merge_queue.pop(0)
        if task == "summarize_history":
            return history_queue.pop(0)
        raise AssertionError(f"unexpected task: {task}")

    mock = MagicMock(side_effect=fake_complete)
    monkeypatch.setattr(profile_service.llm, "complete", mock)
    return mock


# ---------------------------------------------------------------------------
# verify_quote / _normalize_whitespace
# ---------------------------------------------------------------------------

def test_normalize_whitespace_collapses_and_strips():
    assert profile_service._normalize_whitespace("  a\tb\n\nc  ") == "a b c"


def test_verify_quote_true_despite_whitespace_differences():
    source = "Line one.\n\n   Line   two has   extra spaces."
    assert profile_service.verify_quote("Line two has extra spaces.", source) is True


def test_verify_quote_false_when_quote_absent():
    assert profile_service.verify_quote("Not in the document", "Something else entirely") is False


def test_verify_quote_false_for_empty_or_blank_quote():
    assert profile_service.verify_quote("", "some source text") is False
    assert profile_service.verify_quote("   ", "some source text") is False


# ---------------------------------------------------------------------------
# _parse_json_response
# ---------------------------------------------------------------------------

def test_parse_json_response_plain_json():
    assert profile_service._parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_strips_json_fence():
    raw = "```json\n{\"a\": 1}\n```"
    assert profile_service._parse_json_response(raw) == {"a": 1}


def test_parse_json_response_raises_llm_response_error_on_garbage():
    with pytest.raises(profile_service.LLMResponseError):
        profile_service._parse_json_response("not json at all")


# ---------------------------------------------------------------------------
# extract_facts
# ---------------------------------------------------------------------------

def test_extract_facts_drops_fact_with_fabricated_quote(monkeypatch):
    source_text = "Jordan Lee is a Software Engineer at Acme Corp."
    response = _extract_response(
        [
            {"section": "summary", "content": "Name is Jordan Lee", "quote": "Jordan Lee"},
            {"section": "experience", "content": "Worked at a FAANG company", "quote": "Worked at Google for 10 years"},
        ]
    )
    _mock_llm(monkeypatch, extract=response)

    candidates = profile_service.extract_facts(source_text)

    contents = [c.content for c in candidates]
    assert "Name is Jordan Lee" in contents
    assert "Worked at a FAANG company" not in contents
    assert len(candidates) == 1


def test_extract_facts_folds_style_observations_into_style_section(monkeypatch):
    source_text = "Built things quickly and concisely."
    response = _extract_response(
        facts=[],
        style_observations=[
            {"content": "Prefers short, punchy sentences", "quote": "Built things quickly and concisely."}
        ],
    )
    _mock_llm(monkeypatch, extract=response)

    candidates = profile_service.extract_facts(source_text)

    assert len(candidates) == 1
    assert candidates[0].section == "style"
    assert candidates[0].content == "Prefers short, punchy sentences"


# ---------------------------------------------------------------------------
# merge_facts
# ---------------------------------------------------------------------------

def test_merge_facts_returns_empty_list_for_no_candidates_without_calling_llm(monkeypatch, tmp_path):
    from src.db.migrate import migrate

    mock = _mock_llm(monkeypatch)
    migrate(db_path=_db_path(tmp_path))
    conn = db_connection.get_connection(_db_path(tmp_path))

    decisions = profile_service.merge_facts(conn, [])

    assert decisions == []
    mock.assert_not_called()
    conn.close()


# ---------------------------------------------------------------------------
# apply_merge_decisions
# ---------------------------------------------------------------------------

def test_apply_merge_decisions_new_inserts_fact_and_provenance(tmp_path):
    from src.db.migrate import migrate
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    doc = repos.insert_document(conn, filename="f.pdf", doc_type="resume", sha256="abc", markdown_text="text")
    candidate = profile_service.CandidateFact(section="skills", content="Knows Python", quote="Python")
    decision = profile_service.MergeDecision(candidate_index=0, decision="new")

    result = profile_service.apply_merge_decisions(
        conn, document_id=doc["id"], candidates=[candidate], decisions=[decision], origin="ingestion"
    )

    assert len(result.new) == 1
    fact = repos.get_fact_by_uid(conn, result.new[0])
    assert fact["content"] == "Knows Python"
    assert fact["status"] == "active"
    source = conn.execute(
        "SELECT * FROM fact_sources WHERE fact_id = ? AND document_id = ?", (fact["id"], doc["id"])
    ).fetchone()
    assert source is not None
    assert source["quote"] == "Python"
    conn.close()


def test_apply_merge_decisions_duplicate_of_adds_provenance_without_new_fact_row(tmp_path):
    from src.db.migrate import migrate
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    doc1 = repos.insert_document(conn, filename="a.pdf", doc_type="resume", sha256="a", markdown_text="text a")
    existing = repos.insert_profile_fact(conn, section="skills", content="Knows Python", origin="ingestion")
    repos.insert_fact_source(conn, fact_id=existing["id"], document_id=doc1["id"], quote="Python")

    doc2 = repos.insert_document(conn, filename="b.pdf", doc_type="resume", sha256="b", markdown_text="text b")
    candidate = profile_service.CandidateFact(section="skills", content="Knows Python", quote="Python")
    decision = profile_service.MergeDecision(candidate_index=0, decision="duplicate_of", uid=existing["uid"])

    before_count = conn.execute("SELECT COUNT(*) AS n FROM profile_facts").fetchone()["n"]
    result = profile_service.apply_merge_decisions(
        conn, document_id=doc2["id"], candidates=[candidate], decisions=[decision], origin="ingestion"
    )
    after_count = conn.execute("SELECT COUNT(*) AS n FROM profile_facts").fetchone()["n"]

    assert after_count == before_count
    assert result.duplicates == [existing["uid"]]
    source = conn.execute(
        "SELECT * FROM fact_sources WHERE fact_id = ? AND document_id = ?", (existing["id"], doc2["id"])
    ).fetchone()
    assert source is not None
    conn.close()


def test_apply_merge_decisions_conflicts_with_keeps_both_facts_active(tmp_path):
    from src.db.migrate import migrate
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    doc1 = repos.insert_document(conn, filename="a.pdf", doc_type="resume", sha256="a", markdown_text="text a")
    existing = repos.insert_profile_fact(conn, section="experience", content="5 years of experience", origin="ingestion")
    repos.insert_fact_source(conn, fact_id=existing["id"], document_id=doc1["id"], quote="5 years")

    doc2 = repos.insert_document(conn, filename="b.pdf", doc_type="resume", sha256="b", markdown_text="text b")
    candidate = profile_service.CandidateFact(section="experience", content="3 years of experience", quote="3 years")
    decision = profile_service.MergeDecision(
        candidate_index=0, decision="conflicts_with", uid=existing["uid"], resolution="differing years claimed"
    )

    result = profile_service.apply_merge_decisions(
        conn, document_id=doc2["id"], candidates=[candidate], decisions=[decision], origin="ingestion"
    )

    assert len(result.conflicts) == 1
    new_uid, existing_uid, resolution = result.conflicts[0]
    assert existing_uid == existing["uid"]
    assert resolution == "differing years claimed"

    old_fact = repos.get_fact_by_uid(conn, existing["uid"])
    new_fact = repos.get_fact_by_uid(conn, new_uid)
    assert old_fact["status"] == "active"
    assert new_fact["status"] == "active"
    conn.close()


# ---------------------------------------------------------------------------
# ingest_document (acceptance-criteria-mapped)
# ---------------------------------------------------------------------------

def _v1_extract_response():
    return _extract_response(
        [
            {"section": "summary", "content": "Name is Jordan Lee", "quote": "Jordan Lee"},
            {"section": "experience", "content": "Software Engineer at Acme Corp", "quote": "Software Engineer at Acme Corp"},
            {"section": "achievements", "content": "Built a scalable data pipeline in Python", "quote": "Built a scalable data pipeline in Python"},
            {"section": "skills", "content": "Proficient in SQL and distributed systems", "quote": "Proficient in SQL and distributed systems"},
        ]
    )


def _all_new_merge_response(n):
    return _merge_response([{"candidate_index": i, "decision": "new"} for i in range(n)])


def test_ingest_document_creates_document_and_facts_with_provenance(monkeypatch, db_path):
    _mock_llm(monkeypatch, extract=_v1_extract_response(), merge=_all_new_merge_response(4))

    result = profile_service.ingest_document(str(SAMPLE_V1), doc_type="resume", db_path=db_path)

    assert result.skipped is False
    assert len(result.apply.new) == 4

    conn = db_connection.get_connection(db_path)
    doc_row = conn.execute("SELECT * FROM documents WHERE id = ?", (result.document_id,)).fetchone()
    assert doc_row is not None
    assert doc_row["doc_type"] == "resume"

    fact_count = conn.execute("SELECT COUNT(*) AS n FROM profile_facts").fetchone()["n"]
    assert fact_count == 4

    source_count = conn.execute("SELECT COUNT(*) AS n FROM fact_sources").fetchone()["n"]
    assert source_count == 4
    conn.close()


def test_ingest_document_reingest_is_noop(monkeypatch, db_path):
    mock = _mock_llm(monkeypatch, extract=_v1_extract_response(), merge=_all_new_merge_response(4))

    first = profile_service.ingest_document(str(SAMPLE_V1), doc_type="resume", db_path=db_path)
    assert first.skipped is False
    calls_after_first = mock.call_count

    second = profile_service.ingest_document(str(SAMPLE_V1), doc_type="resume", db_path=db_path)

    assert second.skipped is True
    assert second.document_id == first.document_id
    assert mock.call_count == calls_after_first  # no new LLM calls

    conn = db_connection.get_connection(db_path)
    doc_count = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    fact_count = conn.execute("SELECT COUNT(*) AS n FROM profile_facts").fetchone()["n"]
    assert doc_count == 1
    assert fact_count == 4
    conn.close()


def test_ingest_document_supersedes_on_updated_job_title(monkeypatch, db_path):
    _mock_llm(monkeypatch, extract=_v1_extract_response(), merge=_all_new_merge_response(4))
    profile_service.ingest_document(str(SAMPLE_V1), doc_type="resume", db_path=db_path)

    conn = db_connection.get_connection(db_path)
    old_title_fact = conn.execute(
        "SELECT * FROM profile_facts WHERE content = ?", ("Software Engineer at Acme Corp",)
    ).fetchone()
    other_uids = {
        row["content"]: row["uid"]
        for row in conn.execute("SELECT * FROM profile_facts WHERE status = 'active'").fetchall()
        if row["id"] != old_title_fact["id"]
    }
    conn.close()

    v2_extract = _extract_response(
        [
            {"section": "summary", "content": "Name is Jordan Lee", "quote": "Jordan Lee"},
            {"section": "experience", "content": "Senior Software Engineer at Acme Corp", "quote": "Senior Software Engineer at Acme Corp"},
            {"section": "achievements", "content": "Built a scalable data pipeline in Python", "quote": "Built a scalable data pipeline in Python"},
            {"section": "skills", "content": "Proficient in SQL and distributed systems", "quote": "Proficient in SQL and distributed systems"},
        ]
    )
    v2_merge = _merge_response(
        [
            {"candidate_index": 0, "decision": "duplicate_of", "uid": other_uids["Name is Jordan Lee"]},
            {"candidate_index": 1, "decision": "updates", "uid": old_title_fact["uid"], "resolution": "promoted to Senior"},
            {"candidate_index": 2, "decision": "duplicate_of", "uid": other_uids["Built a scalable data pipeline in Python"]},
            {"candidate_index": 3, "decision": "duplicate_of", "uid": other_uids["Proficient in SQL and distributed systems"]},
        ]
    )
    _mock_llm(monkeypatch, extract=v2_extract, merge=v2_merge)

    result = profile_service.ingest_document(str(SAMPLE_V2), doc_type="resume", db_path=db_path)

    assert result.skipped is False
    assert len(result.apply.updated) == 1

    conn = db_connection.get_connection(db_path)
    old_row = conn.execute("SELECT * FROM profile_facts WHERE id = ?", (old_title_fact["id"],)).fetchone()
    new_uid = result.apply.updated[0]
    new_row = conn.execute("SELECT * FROM profile_facts WHERE uid = ?", (new_uid,)).fetchone()

    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == new_row["id"]
    assert new_row["status"] == "active"
    assert new_row["content"] == "Senior Software Engineer at Acme Corp"

    old_source = conn.execute(
        "SELECT * FROM fact_sources WHERE fact_id = ?", (old_row["id"],)
    ).fetchone()
    new_source = conn.execute(
        "SELECT * FROM fact_sources WHERE fact_id = ?", (new_row["id"],)
    ).fetchone()
    assert old_source["document_id"] != new_source["document_id"]
    conn.close()


def test_ingest_document_raises_for_suspiciously_short_document(monkeypatch, db_path):
    monkeypatch.setattr(profile_service, "_convert_to_markdown", lambda path: "")
    mock = _mock_llm(monkeypatch)

    with pytest.raises(profile_service.SuspiciousDocumentError):
        profile_service.ingest_document("whatever.pdf", doc_type="resume", db_path=db_path)

    mock.assert_not_called()
    conn = db_connection.get_connection(db_path)
    doc_count = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert doc_count == 0
    conn.close()


# ---------------------------------------------------------------------------
# seed_profile
# ---------------------------------------------------------------------------

def test_seed_profile_builds_blob_with_section_headers_and_seed_origin(monkeypatch, db_path):
    captured_prompts = {}

    def fake_complete(*, task, system, user, max_tokens=4096, model=None):
        captured_prompts[task] = user
        if task == "extract_facts":
            return _extract_response(
                [{"section": "summary", "content": "Backend engineer", "quote": "I am a backend engineer"}]
            )
        if task == "merge_facts":
            return _all_new_merge_response(1)
        raise AssertionError(task)

    monkeypatch.setattr(profile_service.llm, "complete", MagicMock(side_effect=fake_complete))

    answers = {"summary": "I am a backend engineer", "skills": "Python, SQL"}
    result = profile_service.seed_profile(answers, db_path=db_path)

    assert result.skipped is False
    assert "## summary" in captured_prompts["extract_facts"]
    assert "## skills" in captured_prompts["extract_facts"]
    assert captured_prompts["extract_facts"].index("## summary") < captured_prompts["extract_facts"].index("## skills")

    conn = db_connection.get_connection(db_path)
    doc_row = conn.execute("SELECT * FROM documents WHERE id = ?", (result.document_id,)).fetchone()
    assert doc_row["doc_type"] == "notes"
    fact_row = conn.execute("SELECT * FROM profile_facts LIMIT 1").fetchone()
    assert fact_row["origin"] == "seed"
    conn.close()


def test_seed_profile_raises_on_empty_answers():
    with pytest.raises(ValueError):
        profile_service.seed_profile({})


def test_seed_profile_raises_when_all_answers_blank():
    with pytest.raises(ValueError):
        profile_service.seed_profile({"summary": "   "})


def test_seed_profile_reingest_same_answers_is_noop(monkeypatch, db_path):
    mock = _mock_llm(monkeypatch, extract=_extract_response([]), merge=_merge_response([]))

    answers = {"summary": "Backend engineer"}
    first = profile_service.seed_profile(answers, db_path=db_path)
    assert first.skipped is False

    second = profile_service.seed_profile(dict(answers), db_path=db_path)
    assert second.skipped is True
    assert second.document_id == first.document_id


# ---------------------------------------------------------------------------
# Phase 1 bugfix: apply_merge_decisions must not re-supersede an
# already-superseded fact within the same batch
# ---------------------------------------------------------------------------

def test_apply_merge_decisions_second_update_to_same_uid_falls_through_to_new(tmp_path):
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    doc = repos.insert_document(conn, filename="f.pdf", doc_type="resume", sha256="abc", markdown_text="text")
    original = repos.insert_profile_fact(conn, section="experience", content="Engineer", origin="ingestion")

    candidates = [
        profile_service.CandidateFact(section="experience", content="Senior Engineer", quote="Senior Engineer"),
        profile_service.CandidateFact(section="experience", content="Staff Engineer", quote="Staff Engineer"),
    ]
    decisions = [
        profile_service.MergeDecision(candidate_index=0, decision="updates", uid=original["uid"]),
        profile_service.MergeDecision(candidate_index=1, decision="updates", uid=original["uid"]),
    ]

    result = profile_service.apply_merge_decisions(
        conn, document_id=doc["id"], candidates=candidates, decisions=decisions, origin="ingestion"
    )

    assert len(result.updated) == 1
    assert len(result.new) == 1

    first_update = repos.get_fact_by_uid(conn, result.updated[0])
    ancestors = repos.get_superseded_ancestors(conn, first_update["id"])
    assert [a["uid"] for a in ancestors] == [original["uid"]]

    # The second candidate's own fact has no ancestors - it never
    # touched original's chain.
    second_new = repos.get_fact_by_uid(conn, result.new[0])
    assert repos.get_superseded_ancestors(conn, second_new["id"]) == []
    conn.close()


# ---------------------------------------------------------------------------
# repos: sync_state / supersede_fact(new_fact_id=None) / chain-walking
# ---------------------------------------------------------------------------

def test_supersede_fact_with_no_new_fact_id_sets_null(tmp_path):
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    fact = repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    repos.supersede_fact(conn, old_fact_id=fact["id"], new_fact_id=None)

    updated = repos.get_fact_by_uid(conn, fact["uid"])
    assert updated["status"] == "superseded"
    assert updated["superseded_by"] is None
    conn.close()


def test_get_superseded_ancestors_oldest_first_and_empty_for_no_ancestors(tmp_path):
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    fact1 = repos.insert_profile_fact(conn, section="skills", content="v1", origin="seed")
    fact2 = repos.insert_profile_fact(conn, section="skills", content="v2", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact1["id"], new_fact_id=fact2["id"])
    fact3 = repos.insert_profile_fact(conn, section="skills", content="v3", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact2["id"], new_fact_id=fact3["id"])

    ancestors = repos.get_superseded_ancestors(conn, fact3["id"])
    assert [a["uid"] for a in ancestors] == [fact1["uid"], fact2["uid"]]
    assert repos.get_superseded_ancestors(conn, fact1["id"]) == []
    conn.close()


def test_resolve_live_descendant_active_row_and_dead_end(tmp_path):
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    fact1 = repos.insert_profile_fact(conn, section="skills", content="v1", origin="seed")
    fact2 = repos.insert_profile_fact(conn, section="skills", content="v2", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact1["id"], new_fact_id=fact2["id"])

    descendant = repos.resolve_live_descendant(conn, fact1["id"])
    assert descendant["uid"] == fact2["uid"]

    repos.supersede_fact(conn, old_fact_id=fact2["id"], new_fact_id=None)
    assert repos.resolve_live_descendant(conn, fact1["id"]) is None
    conn.close()


def test_get_set_sync_state_upserts(tmp_path):
    from src.db import repos

    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    conn = db_connection.get_connection(db_path)

    assert repos.get_sync_state(conn, "last_export_hash") is None
    repos.set_sync_state(conn, "last_export_hash", "abc")
    assert repos.get_sync_state(conn, "last_export_hash") == "abc"
    repos.set_sync_state(conn, "last_export_hash", "def")
    assert repos.get_sync_state(conn, "last_export_hash") == "def"
    conn.close()


# ---------------------------------------------------------------------------
# export_profile / sync_profile (acceptance criteria)
# ---------------------------------------------------------------------------

def test_export_profile_excludes_superseded_facts(db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    active = repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    old = repos.insert_profile_fact(conn, section="skills", content="Old skill", origin="seed")
    repos.supersede_fact(conn, old_fact_id=old["id"], new_fact_id=active["id"])
    conn.commit()
    conn.close()

    result = profile_service.export_profile(db_path=db_path)

    text = result.path.read_text()
    assert "Old skill" not in text
    assert old["uid"] not in text
    assert "Python" in text
    assert active["uid"] in text


def test_sync_profile_updates_edited_deleted_and_added_bullets(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    fact_keep = repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    fact_edit = repos.insert_profile_fact(conn, section="skills", content="SQL", origin="seed")
    fact_delete = repos.insert_profile_fact(conn, section="skills", content="Old skill", origin="seed")
    conn.commit()
    conn.close()

    profile_service.export_profile(db_path=db_path)

    edited = (
        "# Professional Profile\n"
        "_Exported 2026-01-01 00:00 — edit freely, then run `jobsearch profile sync`_\n\n"
        "## Skills\n"
        f"- Python <!-- {fact_keep['uid']} -->\n"
        f"- Advanced SQL <!-- {fact_edit['uid']} -->\n"
        "- Kubernetes\n"
    )
    config.PROFILE_MD_PATH.write_text(edited, encoding="utf-8")

    mock = _mock_llm(monkeypatch)  # sync must make zero LLM calls
    result = profile_service.sync_profile(db_path=db_path)
    mock.assert_not_called()

    assert result.no_op is False
    assert result.deleted == [fact_delete["uid"]]
    assert len(result.updated) == 1
    assert len(result.new) == 1
    assert result.conflicts == []

    conn = db_connection.get_connection(db_path)
    deleted_row = repos.get_fact_by_uid(conn, fact_delete["uid"])
    assert deleted_row["status"] == "superseded"
    assert deleted_row["superseded_by"] is None

    assert repos.get_fact_by_uid(conn, fact_keep["uid"])["status"] == "active"

    updated_fact = repos.get_fact_by_uid(conn, result.updated[0])
    assert updated_fact["content"] == "Advanced SQL"
    assert updated_fact["origin"] == "manual"
    old_edit_row = repos.get_fact_by_uid(conn, fact_edit["uid"])
    assert old_edit_row["status"] == "superseded"
    assert old_edit_row["superseded_by"] == updated_fact["id"]

    new_fact = repos.get_fact_by_uid(conn, result.new[0])
    assert new_fact["content"] == "Kubernetes"
    assert new_fact["origin"] == "manual"
    conn.close()

    # Sync re-exports; the file should now reflect the new DB state with
    # no superseded facts and fresh anchors for the changed bullets.
    final_text = config.PROFILE_MD_PATH.read_text()
    assert "Old skill" not in final_text
    assert "Advanced SQL" in final_text
    assert "Kubernetes" in final_text


def test_sync_profile_conflict_manual_edit_wins_over_ingestion_change(db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    fact_x = repos.insert_profile_fact(conn, section="experience", content="Engineer", origin="seed")
    conn.commit()
    conn.close()

    profile_service.export_profile(db_path=db_path)

    # Simulate an intervening ingestion that superseded X into Y while
    # the file was still clean (bypasses the dirty guard on purpose -
    # this represents "ingestion ran, then the user's stale editor
    # buffer overwrote the auto-exported file on save").
    conn = db_connection.get_connection(db_path)
    doc = repos.insert_document(conn, filename="new.pdf", doc_type="resume", sha256="xyz", markdown_text="text")
    fact_y = repos.insert_profile_fact(conn, section="experience", content="Senior Engineer", origin="ingestion")
    repos.insert_fact_source(conn, fact_id=fact_y["id"], document_id=doc["id"], quote="Senior Engineer")
    repos.supersede_fact(conn, old_fact_id=fact_x["id"], new_fact_id=fact_y["id"])
    conn.commit()
    conn.close()

    edited = (
        "# Professional Profile\n_Exported 2026-01-01 00:00 — edit freely_\n\n"
        "## Experience\n"
        f"- Lead Engineer <!-- {fact_x['uid']} -->\n"
    )
    config.PROFILE_MD_PATH.write_text(edited, encoding="utf-8")

    result = profile_service.sync_profile(db_path=db_path)

    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.original_uid == fact_x["uid"]
    assert conflict.descendant_uid == fact_y["uid"]

    conn = db_connection.get_connection(db_path)
    winner = repos.get_fact_by_uid(conn, conflict.new_uid)
    assert winner["status"] == "active"
    assert winner["content"] == "Lead Engineer"
    assert winner["origin"] == "manual"

    y_row = repos.get_fact_by_uid(conn, fact_y["uid"])
    assert y_row["status"] == "superseded"
    assert y_row["superseded_by"] == winner["id"]

    x_row = repos.get_fact_by_uid(conn, fact_x["uid"])
    assert x_row["status"] == "superseded"
    assert x_row["superseded_by"] == fact_y["id"]  # untouched by the conflict resolution
    conn.close()


def test_sync_profile_noop_when_file_matches_last_export(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    conn.commit()
    conn.close()

    profile_service.export_profile(db_path=db_path)

    mock = _mock_llm(monkeypatch)
    result = profile_service.sync_profile(db_path=db_path)

    assert result.no_op is True
    assert result.new == []
    assert result.updated == []
    assert result.deleted == []
    assert result.conflicts == []
    mock.assert_not_called()


def test_sync_profile_raises_when_never_exported(db_path):
    with pytest.raises(profile_service.ProfileNotExportedError):
        profile_service.sync_profile(db_path=db_path)


def test_ingest_document_raises_dirty_profile_error_when_file_edited_since_export(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    conn.commit()
    conn.close()

    profile_service.export_profile(db_path=db_path)
    config.PROFILE_MD_PATH.write_text(
        config.PROFILE_MD_PATH.read_text() + "\n- Unsynced manual addition\n", encoding="utf-8"
    )

    monkeypatch.setattr(profile_service, "_convert_to_markdown", lambda path: "Real resume content. " * 20)
    mock = _mock_llm(monkeypatch)

    with pytest.raises(profile_service.DirtyProfileError):
        profile_service.ingest_document("whatever.pdf", doc_type="resume", db_path=db_path)

    mock.assert_not_called()
    conn = db_connection.get_connection(db_path)
    doc_count = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert doc_count == 0
    conn.close()


def test_seed_profile_raises_dirty_profile_error_when_file_edited_since_export(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    conn.commit()
    conn.close()

    profile_service.export_profile(db_path=db_path)
    config.PROFILE_MD_PATH.write_text(
        config.PROFILE_MD_PATH.read_text() + "\n- Unsynced manual addition\n", encoding="utf-8"
    )

    mock = _mock_llm(monkeypatch)

    with pytest.raises(profile_service.DirtyProfileError):
        profile_service.seed_profile({"summary": "Backend engineer"}, db_path=db_path)

    mock.assert_not_called()


def test_ingest_document_succeeds_when_profile_never_exported(monkeypatch, db_path):
    _mock_llm(monkeypatch, extract=_v1_extract_response(), merge=_all_new_merge_response(4))

    result = profile_service.ingest_document(str(SAMPLE_V1), doc_type="resume", db_path=db_path)

    assert result.skipped is False
    assert config.PROFILE_MD_PATH.exists()  # auto-export created it


# ---------------------------------------------------------------------------
# profile show --history (acceptance criteria)
# ---------------------------------------------------------------------------

def test_get_profile_facts_with_history_skips_llm_for_zero_ancestor_chain(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    repos.insert_profile_fact(conn, section="skills", content="Python", origin="seed")
    conn.commit()
    conn.close()

    mock = _mock_llm(monkeypatch)
    entries = profile_service.get_profile_facts_with_history(db_path=db_path)

    assert entries[0]["history_summary"] is None
    mock.assert_not_called()

    conn = db_connection.get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM history_summaries").fetchone()["n"]
    assert count == 0
    conn.close()


def test_get_profile_facts_with_history_renders_cached_summary_and_caches_llm_call(monkeypatch, db_path):
    """
    Literal acceptance wording: "a fact with two superseded ancestors
    renders a cached one-line lineage" - uses a 3-node chain (2
    ancestors + 1 active head) so both the ancestor-count and the
    cache-hit-on-repeat properties are proven together, not separately.
    """
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    fact1 = repos.insert_profile_fact(conn, section="experience", content="Worked on billing", origin="ingestion")
    fact2 = repos.insert_profile_fact(conn, section="experience", content="Worked on billing infra", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact1["id"], new_fact_id=fact2["id"])
    fact3 = repos.insert_profile_fact(conn, section="experience", content="Led billing infra migration", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact2["id"], new_fact_id=fact3["id"])
    conn.commit()
    conn.close()

    mock = _mock_llm(monkeypatch, summarize_history="evolved: billing work over time")

    entries = profile_service.get_profile_facts_with_history(db_path=db_path)
    entries_again = profile_service.get_profile_facts_with_history(db_path=db_path)

    conn = db_connection.get_connection(db_path)
    assert len(repos.get_superseded_ancestors(conn, entries[0]["fact"]["id"])) == 2
    conn.close()

    assert entries[0]["history_summary"] == "evolved: billing work over time"
    assert entries_again[0]["history_summary"] == "evolved: billing work over time"
    assert mock.call_count == 1  # second call is a cache hit


def test_history_summary_cache_invalidates_when_chain_grows(monkeypatch, db_path):
    from src.db import repos

    conn = db_connection.get_connection(db_path)
    fact1 = repos.insert_profile_fact(conn, section="experience", content="v1", origin="ingestion")
    fact2 = repos.insert_profile_fact(conn, section="experience", content="v2", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact1["id"], new_fact_id=fact2["id"])
    conn.commit()
    conn.close()

    mock = _mock_llm(monkeypatch, summarize_history=["summary v2", "summary v3"])

    entries = profile_service.get_profile_facts_with_history(db_path=db_path)
    assert entries[0]["history_summary"] == "summary v2"
    assert mock.call_count == 1

    conn = db_connection.get_connection(db_path)
    fact3 = repos.insert_profile_fact(conn, section="experience", content="v3", origin="manual")
    repos.supersede_fact(conn, old_fact_id=fact2["id"], new_fact_id=fact3["id"])
    conn.commit()
    conn.close()

    entries_after_growth = profile_service.get_profile_facts_with_history(db_path=db_path)
    assert entries_after_growth[0]["history_summary"] == "summary v3"
    assert mock.call_count == 2  # natural cache-miss on the new fact_id
