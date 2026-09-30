"""Tests 3-4 from docs/slice-3.md section 12: the web route that kicks off
the orchestrator, and the awaiting_review count on the runs list. The
orchestrator itself is stubbed here - tests/test_orchestrator.py already
covers its actual tool-use loop against a real (in-process) gateway.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from app.main import _build_target_jql
from orchestrator.agent import OrchestratorError


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), spy


def test_agent_console_prefills_scope_from_the_query_string(client):
    """The dashboard's "N stories not yet estimated" suggestion links to
    /agent-console?scope=unestimated so a human doesn't have to re-make a
    choice they were just told the answer to."""
    test_client, _ = client

    resp = test_client.get("/agent-console?scope=all")

    assert resp.status_code == 200
    assert 'value="all" checked' in resp.text
    assert 'value="unestimated" checked' not in resp.text


def test_agent_console_defaults_scope_to_unestimated_with_no_query_string(client):
    test_client, _ = client

    resp = test_client.get("/agent-console")

    assert 'value="unestimated" checked' in resp.text


def test_agent_console_always_submits_at_l2(client):
    """Super-Pilot's wizard no longer offers a level picker - see
    app/levels.py's module docstring for the 3-mode redesign. Every run
    it starts dispatches at L2, via a fixed hidden field."""
    test_client, _ = client

    resp = test_client.get("/agent-console")

    assert 'name="level" value="L2"' in resp.text
    assert 'name="level" value="L1"' not in resp.text
    assert 'name="level" value="L3"' not in resp.text
    assert 'name="level" value="L4"' not in resp.text


def _insert_run(conn, run_id: str, status: str) -> None:
    conn.execute(
        "INSERT INTO runs (id, level, task_type, agent, scope, created_at, status) "
        "VALUES (?, 'L3', 'reestimate', 'planning', 'project = TEST', ?, ?)",
        (run_id, datetime.now(timezone.utc).isoformat(), status),
    )
    conn.commit()


def test_agent_console_run_creates_a_run_and_redirects(client, conn, monkeypatch):
    test_client, _ = client

    async def fake_run_reestimate_task(*, level, target_jql, instructions):
        _insert_run(conn, "fake-run-id", "awaiting_review")
        return "fake-run-id"

    monkeypatch.setattr(app_main, "run_reestimate_task", fake_run_reestimate_task)

    resp = test_client.post(
        "/agent-console/run",
        data={"scope": "unestimated", "instructions": "", "level": "L3"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/runs/fake-run-id"


def test_agent_console_run_surfaces_orchestrator_failure_as_502(client, monkeypatch):
    """L1 used to be this test's example (it had no write tools, so the
    orchestrator reliably raised OrchestratorError) - now L1 never reaches
    the orchestrator at all (see the redirect test below), so this uses L2
    with a stubbed failure instead to exercise the same 502 path for a
    level that does reach run_reestimate_task."""
    test_client, _ = client

    async def failing_run_reestimate_task(*, level, target_jql, instructions):
        raise OrchestratorError(f"Gemini never called finish_run for this task at level={level!r}")

    monkeypatch.setattr(app_main, "run_reestimate_task", failing_run_reestimate_task)

    resp = test_client.post(
        "/agent-console/run",
        data={"scope": "unestimated", "instructions": "", "level": "L2"},
    )

    assert resp.status_code == 502
    assert "never called finish_run" in resp.text


def test_agent_console_run_redirects_l1_to_chat_without_calling_the_orchestrator(client, monkeypatch):
    """L1 has no start_run tool - see TOOLS_BY_LEVEL in gateway/server.py -
    so this route must never hand it to run_reestimate_task at all, not
    even to let it fail with a 502. The wizard itself no longer offers L1
    (Co-Pilot lives entirely at /chat); this guards anyone who posts here
    directly with level=L1, or has JS disabled and somehow still does."""
    test_client, _ = client

    async def unexpected_run_reestimate_task(*, level, target_jql, instructions):
        raise AssertionError("run_reestimate_task must not be called for level=L1")

    monkeypatch.setattr(app_main, "run_reestimate_task", unexpected_run_reestimate_task)

    resp = test_client.post(
        "/agent-console/run",
        data={"scope": "unestimated", "instructions": "", "level": "L1"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/chat"


def test_agent_console_run_rejects_empty_specific_issues(client):
    """A naive user picking 'Specific issue(s)' without typing any key gets
    a clear 400, not a JQL syntax error from Jira three layers down."""
    test_client, _ = client

    resp = test_client.post(
        "/agent-console/run",
        data={"scope": "specific", "specific_issues": "", "instructions": "", "level": "L3"},
    )

    assert resp.status_code == 400
    assert "issue key" in resp.text


def test_build_target_jql_unestimated(jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    monkeypatch.setattr(app_main, "_jira", jira)

    jql = _build_target_jql("unestimated", "")

    assert jql == f'project = {jira.config.project_key} AND issuetype = Story AND cf[10016] is EMPTY'


def test_build_target_jql_all(jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    monkeypatch.setattr(app_main, "_jira", jira)

    jql = _build_target_jql("all", "")

    assert jql == f"project = {jira.config.project_key} AND issuetype = Story"


def test_build_target_jql_specific_parses_and_normalises_keys(jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    monkeypatch.setattr(app_main, "_jira", jira)

    jql = _build_target_jql("specific", "mcp-4, mcp-5  mcp-6")

    assert jql == "key in (MCP-4, MCP-5, MCP-6)"


def test_build_target_jql_specific_with_no_keys_raises(jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    monkeypatch.setattr(app_main, "_jira", jira)

    with pytest.raises(ValueError, match="issue key"):
        _build_target_jql("specific", "   ")


def test_index_reports_awaiting_review_count(client, conn):
    test_client, _ = client
    _insert_run(conn, "r1", "awaiting_review")
    _insert_run(conn, "r2", "awaiting_review")
    _insert_run(conn, "r3", "applied")

    resp = test_client.get("/approvals")

    assert resp.status_code == 200
    assert "2 waiting for you" in resp.text


def test_index_shows_no_badge_when_nothing_awaiting_review(client, conn):
    test_client, _ = client
    _insert_run(conn, "r1", "applied")

    resp = test_client.get("/approvals")

    assert resp.status_code == 200
    assert "0 waiting for you" in resp.text
    assert 'class="queue-card' not in resp.text
