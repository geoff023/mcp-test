"""Server-side guardrails: validators every write path must pass through.

Guardrails sit above the autonomy levels (see CLAUDE.md section 2) - they
apply identically at L1 and L4. An agent never negotiates with a guardrail:
the call is refused and the refusal is logged. Implemented as validator
functions called by every write path, not as a check bolted onto one tool.

Nothing here prints to stdout - see gateway/jira.py's docstring for why.
"""

from __future__ import annotations

import sqlite3

from db.audit import log_audit
from gateway.jira import Issue

MILESTONE_LABEL = "milestone"
MILESTONE_ISSUE_TYPE = "Epic"


class GuardrailViolation(Exception):
    """Raised when a write is refused by a guardrail.

    Carries the guardrail's name so callers (MCP tools, later the web app)
    can return a structured error naming which guardrail fired, rather than
    a bare exception message.
    """

    def __init__(self, guardrail: str, message: str):
        self.guardrail = guardrail
        self.message = message
        super().__init__(message)


def is_milestone(issue: Issue) -> bool:
    """An issue counts as a milestone if it's an Epic or carries the
    'milestone' label - either is sufficient."""
    return issue.issue_type == MILESTONE_ISSUE_TYPE or MILESTONE_LABEL in issue.labels


def check_no_milestone_date_change(
    conn: sqlite3.Connection,
    issue: Issue,
    *,
    run_id: str | None,
    actor: str,
    level: str | None,
) -> None:
    """GUARDRAIL: no_milestone_date_change.

    Refuses any due-date change on an issue whose type is Epic or which
    carries the 'milestone' label. Applies at every autonomy level,
    including L4 - there is no bypass.

    On refusal this writes the audit_log row itself before raising, so the
    refusal is on record even though the caller never gets a chance to
    (and the change is never partially applied - the row is written and
    the exception raised in the same call, nothing in between touches Jira).
    """
    if not is_milestone(issue):
        return

    detail = (
        f"refused due-date change on {issue.key}: "
        f"issue_type={issue.issue_type!r}, labels={issue.labels!r}"
    )
    log_audit(
        conn,
        run_id=run_id,
        actor=actor,
        level=level,
        action="propose_due_date_change",
        outcome="refused",
        detail=detail,
    )
    raise GuardrailViolation(
        guardrail="no_milestone_date_change",
        message=(
            f"{issue.key} is a milestone (issue_type={issue.issue_type}, "
            f"labels={issue.labels}); due-date changes are refused at every level."
        ),
    )
