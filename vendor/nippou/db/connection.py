"""SQLite connection helper.

WAL journal mode is enabled so waitress's request threads and any
background sync work (Access import/push) can read/write concurrently
without the
"database is locked" errors that plain rollback-journal mode is prone to
under this app's autosave-every-focus-loss access pattern. WAL setup goes
through :mod:`dbkit.sqlite_toolkit` so the "fall back quietly if a shared
folder can't provide WAL" handling is shared with the other VBA migration
projects instead of re-implemented here.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dbkit.sqlite_toolkit import enable_wal

from .schema import ensure_schema


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    enable_wal(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Explicit transaction context: commits on success, rolls back on
    any exception (mirrors the atomic DELETE+INSERT the VBA
    ``NippouDB_Save`` performed via ``ExecuteSQLTransaction``)."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
