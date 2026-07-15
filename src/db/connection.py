"""
SQLite connection management.

This is the only module in the codebase that should call sqlite3.connect
directly - everything else (migrations, repos, services) goes through
get_connection()/connect() so pragmas and row handling stay consistent,
and so tests can point at a throwaway database by passing db_path.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

DB_PATH = Path(__file__).parent.parent.parent / "data" / "jobsearch.db"


def get_connection(db_path: Optional[Union[Path, str]] = None) -> sqlite3.Connection:
    """
    Open a SQLite connection with the pragmas this project relies on.

    Args:
        db_path: Path to the database file. Defaults to DB_PATH. Tests
            should pass a tmp_path-based path instead of touching the
            real database.

    Returns:
        A connection with row_factory=sqlite3.Row, foreign key
        enforcement, and WAL journaling enabled.
    """
    resolved = Path(db_path) if db_path is not None else DB_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect(db_path: Optional[Union[Path, str]] = None) -> Iterator[sqlite3.Connection]:
    """
    Context manager wrapping get_connection() with commit/rollback/close.

    Usage:
        with connect() as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
