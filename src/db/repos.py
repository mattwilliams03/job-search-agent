"""
Typed database helpers.

The schema/status introspection functions below (list_tables,
table_row_counts, get_schema_status) were the whole module in Phase 0,
when nothing performed CRUD against the business tables yet. Phase 1
adds the first real CRUD - documents/profile_facts/fact_sources - for
src.core.profile_service. Add new per-table functions here as later
phases need them; the introspection functions above stay as-is.
"""

import random
import sqlite3
import string
from pathlib import Path
from typing import Dict, List, Optional, Union

from src.db.connection import get_connection
from src.db.migrate import status as migration_status


def list_tables(conn) -> List[str]:
    """Names of all user tables (excludes sqlite's own internal tables)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def table_row_counts(conn) -> Dict[str, int]:
    """
    Row count per table. Table names come only from sqlite_master (never
    user input), so the f-string below is safe from injection.
    """
    counts = {}
    for name in list_tables(conn):
        counts[name] = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
    return counts


def get_schema_status(db_path: Optional[Union[Path, str]] = None) -> dict:
    """
    Combine migration status with table/row-count info into the dict
    `jobsearch db status` prints.
    """
    mig_status = migration_status(db_path)
    conn = get_connection(db_path)
    try:
        tables = table_row_counts(conn)
    finally:
        conn.close()

    return {
        "db_path": mig_status["db_path"],
        "applied_migrations": mig_status["applied"],
        "pending_migrations": mig_status["pending"],
        "tables": tables,
    }


# =============================================================================
# PROFILE INGESTION: documents / profile_facts / fact_sources
# =============================================================================

def _generate_fact_uid() -> str:
    """Short stable id for a profile fact, e.g. 'f_a3k9'."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"f_{suffix}"


def insert_document(conn, *, filename: str, doc_type: str, sha256: str, markdown_text: str) -> sqlite3.Row:
    """
    Insert a new documents row.

    Does not check for an existing row with the same sha256 - dedup is a
    distinct pipeline step (see profile_service), performed by the
    caller via get_document_by_sha256 before calling this.
    """
    cursor = conn.execute(
        "INSERT INTO documents (filename, doc_type, sha256, markdown_text) VALUES (?, ?, ?, ?)",
        (filename, doc_type, sha256, markdown_text),
    )
    return conn.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()


def get_document_by_sha256(conn, sha256: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()


def insert_profile_fact(conn, *, section: str, content: str, origin: str) -> sqlite3.Row:
    """
    Insert a new active profile fact with a freshly generated uid.

    Retries on a uid collision (UNIQUE(uid) violation) only - any other
    IntegrityError (e.g. a bad `section`/`origin` CHECK value) is a real
    caller bug and propagates immediately.
    """
    last_error: Optional[sqlite3.IntegrityError] = None
    for _ in range(20):
        uid = _generate_fact_uid()
        try:
            cursor = conn.execute(
                "INSERT INTO profile_facts (uid, section, content, origin) VALUES (?, ?, ?, ?)",
                (uid, section, content, origin),
            )
        except sqlite3.IntegrityError as e:
            if "profile_facts.uid" in str(e):
                last_error = e
                continue
            raise
        return conn.execute("SELECT * FROM profile_facts WHERE id = ?", (cursor.lastrowid,)).fetchone()

    raise RuntimeError("failed to generate a unique fact uid after 20 attempts") from last_error


def get_fact_by_uid(conn, uid: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM profile_facts WHERE uid = ?", (uid,)).fetchone()


def get_active_facts(conn, sections: Optional[List[str]] = None) -> List[sqlite3.Row]:
    """Active profile facts, optionally filtered to the given sections."""
    if sections:
        placeholders = ", ".join("?" * len(sections))
        query = (
            f"SELECT * FROM profile_facts WHERE status = 'active' "
            f"AND section IN ({placeholders}) ORDER BY section, id"
        )
        return conn.execute(query, tuple(sections)).fetchall()

    return conn.execute(
        "SELECT * FROM profile_facts WHERE status = 'active' ORDER BY section, id"
    ).fetchall()


def supersede_fact(conn, *, old_fact_id: int, new_fact_id: Optional[int] = None) -> None:
    """
    Mark a fact superseded. new_fact_id=None retires it with no
    replacement (e.g. a bullet deleted during sync).
    """
    conn.execute(
        "UPDATE profile_facts SET status = 'superseded', superseded_by = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (new_fact_id, old_fact_id),
    )


def insert_fact_source(conn, *, fact_id: int, document_id: int, quote: Optional[str]) -> None:
    """
    Record that a document supports a fact. INSERT OR IGNORE guards the
    (fact_id, document_id) composite primary key defensively - e.g. two
    candidates in one merge batch both resolving duplicate_of the same
    existing fact from the same new document.
    """
    conn.execute(
        "INSERT OR IGNORE INTO fact_sources (fact_id, document_id, quote) VALUES (?, ?, ?)",
        (fact_id, document_id, quote),
    )


# =============================================================================
# PROFILE EXPORT / SYNC: sync_state / history_summaries
# =============================================================================

def get_sync_state(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_sync_state(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, value))


def get_fact_by_id(conn, fact_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM profile_facts WHERE id = ?", (fact_id,)).fetchone()


def get_superseded_ancestors(conn, fact_id: int) -> List[sqlite3.Row]:
    """
    Walk the supersede chain backward from fact_id, collecting every
    ancestor that was eventually superseded into it (directly or
    transitively). Returns oldest-first, excluding fact_id itself.

    Uses fetchone() deliberately (not fetchall()) - assumes at most one
    child per node, which insert_profile_fact/supersede_fact call sites
    must maintain (only supersede a currently-active fact).
    """
    ancestors = []
    current_id = fact_id
    while True:
        parent = conn.execute(
            "SELECT * FROM profile_facts WHERE superseded_by = ?", (current_id,)
        ).fetchone()
        if parent is None:
            break
        ancestors.append(parent)
        current_id = parent["id"]

    ancestors.reverse()
    return ancestors


def resolve_live_descendant(conn, fact_id: int) -> Optional[sqlite3.Row]:
    """
    Walk the supersede chain forward from fact_id to the current live
    fact. Returns the active row, or None if the chain dead-ends at a
    fact retired with no replacement (superseded_by IS NULL).
    """
    current_id = fact_id
    for _ in range(10_000):  # defensive cap; chains grow one link at a time
        row = get_fact_by_id(conn, current_id)
        if row is None:
            return None
        if row["status"] == "active":
            return row
        if row["superseded_by"] is None:
            return None
        current_id = row["superseded_by"]
    return None


def get_fact_source_document(conn, fact_id: int) -> Optional[sqlite3.Row]:
    """The (first) document backing a fact, or None for manual-origin facts."""
    return conn.execute(
        "SELECT d.* FROM fact_sources fs JOIN documents d ON d.id = fs.document_id "
        "WHERE fs.fact_id = ? ORDER BY fs.document_id ASC LIMIT 1",
        (fact_id,),
    ).fetchone()


def get_history_summary(conn, fact_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM history_summaries WHERE fact_id = ?", (fact_id,)).fetchone()


def set_history_summary(conn, *, fact_id: int, summary: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO history_summaries (fact_id, summary, computed_at) "
        "VALUES (?, ?, datetime('now'))",
        (fact_id, summary),
    )
