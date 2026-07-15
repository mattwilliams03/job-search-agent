"""
Tests for the SQLite DB layer: connection, migrations, and repos.
"""

import sqlite3

import pytest

from src.db.connection import get_connection
from src.db.migrate import migrate, status
from src.db.repos import get_schema_status, list_tables, table_row_counts

BUSINESS_TABLES = {
    "documents",
    "profile_facts",
    "fact_sources",
    "sync_state",
    "history_summaries",
    "searches",
    "listings",
    "search_results",
    "watched_companies",
    "applications",
    "application_citations",
    "interviews",
    "interview_outcomes",
    "skills_analyses",
}


def _db_path(tmp_path):
    return tmp_path / "test.db"


def test_migrate_creates_all_tables(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    conn = get_connection(db_path)
    try:
        tables = set(list_tables(conn))
    finally:
        conn.close()

    assert BUSINESS_TABLES.issubset(tables)
    assert "schema_migrations" in tables


def test_migrate_is_idempotent(tmp_path):
    db_path = _db_path(tmp_path)

    first = migrate(db_path=db_path)
    second = migrate(db_path=db_path)

    assert first == ["001_initial"]
    assert second == []

    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    finally:
        conn.close()
    assert count == 1


def test_foreign_keys_enforced(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fact_sources (fact_id, document_id) VALUES (999, 999)"
            )
    finally:
        conn.close()


def test_check_constraints_enforced(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    conn = get_connection(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO documents (filename, doc_type, sha256, markdown_text) "
                "VALUES ('r.pdf', 'not_a_real_type', 'abc123', 'text')"
            )
    finally:
        conn.close()


def test_history_summaries_table_fk_shape(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    conn = get_connection(db_path)
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_list(history_summaries)").fetchall()
        pk_columns = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(history_summaries)").fetchall()
            if row["pk"] == 1
        ]
    finally:
        conn.close()

    assert pk_columns == ["fact_id"]
    assert len(fk_rows) == 1
    assert fk_rows[0]["table"] == "profile_facts"
    assert fk_rows[0]["from"] == "fact_id"


def test_status_reports_pending_before_migrate(tmp_path):
    db_path = _db_path(tmp_path)
    result = status(db_path=db_path)

    assert result["applied"] == []
    assert result["pending"] == ["001_initial"]


def test_status_reports_applied_after_migrate(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)
    result = status(db_path=db_path)

    assert result["applied"] == ["001_initial"]
    assert result["pending"] == []


def test_get_schema_status_row_counts_are_zero_after_migrate(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    schema_status = get_schema_status(db_path=db_path)

    assert schema_status["applied_migrations"] == ["001_initial"]
    assert schema_status["pending_migrations"] == []
    for table in BUSINESS_TABLES:
        assert schema_status["tables"][table] == 0


def test_table_row_counts_reflects_inserts(tmp_path):
    db_path = _db_path(tmp_path)
    migrate(db_path=db_path)

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO documents (filename, doc_type, sha256, markdown_text) "
            "VALUES ('resume.pdf', 'resume', 'deadbeef', 'some text')"
        )
        conn.commit()
        counts = table_row_counts(conn)
    finally:
        conn.close()

    assert counts["documents"] == 1
