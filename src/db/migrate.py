"""
Tiny, ordered-SQL-file migration runner.

No Alembic - at this scale, applying numbered *.sql files once each and
tracking what's been applied in a schema_migrations table is sufficient.
"""

from pathlib import Path
from typing import List, Optional, Tuple, Union

from src.db import connection as db_connection
from src.db.connection import get_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> List[Tuple[str, Path]]:
    """Return (version, path) pairs for every *.sql file, sorted by filename."""
    return [(path.stem, path) for path in sorted(migrations_dir.glob("*.sql"))]


def _ensure_tracking_table(conn) -> None:
    conn.executescript(_SCHEMA_MIGRATIONS_DDL)


def applied_versions(conn) -> set:
    _ensure_tracking_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def migrate(
    db_path: Optional[Union[Path, str]] = None,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> List[str]:
    """
    Apply every migration not yet recorded in schema_migrations, in order.

    Returns:
        List of newly-applied version strings. Empty on a second run
        against the same database (idempotent).
    """
    conn = get_connection(db_path)
    try:
        already = applied_versions(conn)
        newly_applied = []

        for version, path in discover_migrations(migrations_dir):
            if version in already:
                continue
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
            conn.commit()
            newly_applied.append(version)

        return newly_applied
    finally:
        conn.close()


def status(
    db_path: Optional[Union[Path, str]] = None,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> dict:
    """
    Report which migrations are applied vs. pending, without applying anything.

    Note: opening a connection (via get_connection) creates the database
    file and its parent directory if they don't exist yet - same
    auto-create behavior as config.OUTPUT_DIR.
    """
    resolved_path = Path(db_path) if db_path is not None else db_connection.DB_PATH
    conn = get_connection(db_path)
    try:
        applied = applied_versions(conn)
        all_versions = {version for version, _ in discover_migrations(migrations_dir)}
        return {
            "db_path": str(resolved_path),
            "applied": sorted(applied),
            "pending": sorted(all_versions - applied),
        }
    finally:
        conn.close()
