"""Applies a run's staged changes to Jira.

Shared by gateway/server.py's commit_changes tool (L4) and app/main.py's
approve route (L3), so both write paths - the only two places in the
system allowed to turn a staged change into a real Jira write - run
exactly the same logic, guardrail check included, instead of two copies
that could quietly drift apart.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from gateway.guardrails import check_no_milestone_date_change
from gateway.jira import JiraClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_staged_changes(
    jira: JiraClient,
    conn: sqlite3.Connection,
    run_id: str,
    *,
    actor: str,
    level: str | None,
) -> list[str]:
    """Apply every not-yet-applied staged change for run_id to Jira.

    Due-date changes are guardrail-checked immediately before being
    applied - not at propose time only - so a milestone can never slip
    through between proposal and commit. Marks each applied change's
    applied_at, but does not touch runs/approval_tokens or commit the
    connection; callers own that, since what "done" means differs between
    the L3 app (mark the run applied) and the L4 tool (also consume a
    token).
    """
    changes = conn.execute(
        "SELECT id, issue_key, field, new_value FROM staged_changes "
        "WHERE run_id = ? AND applied_at IS NULL",
        (run_id,),
    ).fetchall()

    applied: list[str] = []
    for change_id, issue_key, field, new_value in changes:
        if field == "story_points":
            jira.update_issue_fields(issue_key, {jira.config.story_points_field: float(new_value)})
        elif field == "due_date":
            issue = jira.get_issue(issue_key)
            check_no_milestone_date_change(conn, issue, run_id=run_id, actor=actor, level=level)
            jira.update_issue_fields(issue_key, {"duedate": new_value})
        else:
            raise ValueError(f"Unknown staged field {field!r} on {issue_key}")
        conn.execute("UPDATE staged_changes SET applied_at = ? WHERE id = ?", (_now(), change_id))
        applied.append(issue_key)
    return applied
