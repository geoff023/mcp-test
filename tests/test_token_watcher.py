"""find_due_tokens is the token watcher's only non-trivial logic - the
actual commit path (commit_with_token) reuses the exact MCP client call
tests/test_orchestrator.py and tests/test_commit_changes.py already
cover, so it isn't re-tested here.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.token_watcher import find_due_tokens


@pytest.fixture(autouse=True)
def _row_factory(conn):
    conn.row_factory = sqlite3.Row


def _insert_run(conn, run_id: str, level: str, status: str) -> None:
    conn.execute(
        "INSERT INTO runs (id, level, task_type, agent, scope, created_at, status) "
        "VALUES (?, ?, 'reestimate', 'orchestrator:gemini-3.6-flash', 'project = TEST', ?, ?)",
        (run_id, level, datetime.now(timezone.utc).isoformat(), status),
    )


def _insert_token(conn, run_id: str, *, expires_delta: timedelta, consumed: bool = False) -> str:
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at, consumed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            token,
            run_id,
            now.isoformat(),
            (now + expires_delta).isoformat(),
            now.isoformat() if consumed else None,
        ),
    )
    return token


def test_finds_an_unconsumed_unexpired_l4_token(conn):
    _insert_run(conn, "run-1", "L4", "awaiting_review")
    token = _insert_token(conn, "run-1", expires_delta=timedelta(minutes=15))
    conn.commit()

    due = find_due_tokens(conn)

    assert [(r["run_id"], r["token"]) for r in due] == [("run-1", token)]


def test_ignores_a_consumed_token(conn):
    _insert_run(conn, "run-1", "L4", "applied")
    _insert_token(conn, "run-1", expires_delta=timedelta(minutes=15), consumed=True)
    conn.commit()

    assert find_due_tokens(conn) == []


def test_ignores_an_expired_token(conn):
    _insert_run(conn, "run-1", "L4", "awaiting_review")
    _insert_token(conn, "run-1", expires_delta=timedelta(minutes=-1))
    conn.commit()

    assert find_due_tokens(conn) == []


def test_ignores_tokens_on_non_l4_runs(conn):
    # Shouldn't happen in practice (L3's approve issues and consumes a
    # token in the same request), but the watcher must never touch L2/L3
    # write paths regardless.
    _insert_run(conn, "run-1", "L3", "awaiting_review")
    _insert_token(conn, "run-1", expires_delta=timedelta(minutes=15))
    conn.commit()

    assert find_due_tokens(conn) == []


def test_ignores_tokens_on_runs_no_longer_awaiting_review(conn):
    _insert_run(conn, "run-1", "L4", "applied")
    _insert_token(conn, "run-1", expires_delta=timedelta(minutes=15))
    conn.commit()

    assert find_due_tokens(conn) == []


def test_finds_multiple_due_tokens_across_runs(conn):
    _insert_run(conn, "run-1", "L4", "awaiting_review")
    _insert_run(conn, "run-2", "L4", "awaiting_review")
    token1 = _insert_token(conn, "run-1", expires_delta=timedelta(minutes=15))
    token2 = _insert_token(conn, "run-2", expires_delta=timedelta(minutes=15))
    conn.commit()

    due = {(r["run_id"], r["token"]) for r in find_due_tokens(conn)}

    assert due == {("run-1", token1), ("run-2", token2)}
