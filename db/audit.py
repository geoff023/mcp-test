"""Writes to audit_log. The table is append-only: nothing here (or anywhere
else in the codebase) may UPDATE or DELETE a row in it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def log_audit(
    conn: sqlite3.Connection,
    *,
    run_id: str | None,
    actor: str,
    level: str | None,
    action: str,
    outcome: str,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, run_id, actor, level, action, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            run_id,
            actor,
            level,
            action,
            outcome,
            detail,
        ),
    )
    conn.commit()
