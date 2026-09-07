"""SQLite migration runner for the agentic-pm database.

Applies every db/migrations/*.sql file to the database at DB_PATH, in
filename order, exactly once each - tracked in a schema_migrations
bookkeeping table, so it is safe to call on every startup. Importable from
gateway/ without printing anything - printing only happens when this file
is run directly as a script.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _db_path() -> Path:
    raw = os.environ.get("DB_PATH", "./db/agentic_pm.db")
    path = Path(raw)
    return path if path.is_absolute() else _REPO_ROOT / path


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply every migration file not yet recorded in schema_migrations, in
    filename order. Returns the filenames actually applied this call (empty
    if the db was already current). Used both by migrate() below and
    directly by tests, so tests build their DB through the exact same path
    production does rather than a separate schema snapshot.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  filename TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    already_applied = {
        row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    applied: list[str] = []
    for path in _migration_files():
        if path.name in already_applied:
            continue
        conn.executescript(path.read_text())
        conn.execute(
            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (path.name,),
        )
        conn.commit()
        applied.append(path.name)
    return applied


def migrate(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the database at db_path (default: DB_PATH
    from .env), applying any pending migrations first."""
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the MCP server may run tool calls off the
    # main thread. Callers are still responsible for serialising writes
    # (see gateway/server.py's lock) - sqlite3 connections aren't safe for
    # concurrent use from multiple threads even with this flag.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    apply_migrations(conn)
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
