"""
Typed database helpers.

Phase 0 has no service performing CRUD against the business tables yet
(that starts in Phase 1), so the only real consumer here is
`jobsearch db status`. This module is scoped to schema/status
introspection; per-table CRUD (insert_document, get_active_facts, etc.)
gets added here as each later phase gains a real caller.
"""

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
