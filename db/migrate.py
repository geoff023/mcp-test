"""SQLite migration runner for the agentic-pm database.

Applies db/schema.sql to the database at DB_PATH exactly once, tracked in a
schema_migrations bookkeeping table, so it is safe to call on every startup.
Importable from gateway/ without printing anything - printing only happens
when this file is run directly as a script.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _db_path() -> Path:
    raw = os.environ.get("DB_PATH", "./db/agentic_pm.db")
    path = Path(raw)
    return path if path.is_absolute() else _REPO_ROOT / path


def migrate(db_path: Path | None = None) -> sqlite3.Connection:
    """Ensure schema.sql has been applied to db_path (default: DB_PATH from
    .env) and return an open connection to it."""
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the MCP server may run tool calls off the
    # main thread. Callers are still responsible for serialising writes
    # (see gateway/server.py's lock) - sqlite3 connections aren't safe for
    # concurrent use from multiple threads even with this flag.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  filename TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE filename = ?", (SCHEMA_PATH.name,)
    ).fetchone()
    if not applied:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute(
            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (SCHEMA_PATH.name,),
        )
        conn.commit()
    return conn


def get_connection() -> sqlite3.Connection:
    """Open a connection to the database, migrating it first if needed."""
    return migrate()


if __name__ == "__main__":
    connection = migrate()
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"Migrated {_db_path()}")
    for (name,) in tables:
        print(f"  {name}")
    connection.close()
