"""Test 3 from docs/slice-1.md: the milestone guardrail refuses a due-date
change on a milestone issue, at L4, and writes an audit row with outcome
'refused'. Uses an in-memory SQLite db built from the real schema, never a
mock of the guardrail logic itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gateway.guardrails import GuardrailViolation, check_no_milestone_date_change
from gateway.jira import Issue

SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL)
    yield connection
    connection.close()


def _issue(**overrides) -> Issue:
    defaults = dict(
        key="MCP-1",
        summary="Launch",
        status="To Do",
        issue_type="Story",
        story_points=None,
        due_date="2026-01-01",
        labels=[],
    )
    defaults.update(overrides)
    return Issue(**defaults)


def test_refuses_due_date_change_on_epic_at_l4(conn):
    issue = _issue(issue_type="Epic", labels=[])

    with pytest.raises(GuardrailViolation) as exc_info:
        check_no_milestone_date_change(conn, issue, run_id="run-1", actor="agent:planning", level="L4")

    assert exc_info.value.guardrail == "no_milestone_date_change"

    row = conn.execute("SELECT run_id, actor, level, action, outcome FROM audit_log").fetchone()
    assert row == ("run-1", "agent:planning", "L4", "propose_due_date_change", "refused")


def test_refuses_due_date_change_on_milestone_labelled_issue_at_l4(conn):
    issue = _issue(issue_type="Story", labels=["milestone"])

    with pytest.raises(GuardrailViolation):
        check_no_milestone_date_change(conn, issue, run_id="run-2", actor="agent:planning", level="L4")

    outcome = conn.execute("SELECT outcome FROM audit_log WHERE run_id = 'run-2'").fetchone()
    assert outcome == ("refused",)


def test_allows_due_date_change_on_ordinary_story(conn):
    issue = _issue(issue_type="Story", labels=[])

    check_no_milestone_date_change(conn, issue, run_id="run-3", actor="agent:planning", level="L4")

    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
