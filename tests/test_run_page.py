"""Tests for GET /runs/{run_id}'s status_note (app/main.py's _status_note)
and the disclaimer/active-nav additions from the visual/flow pass - no
prior test exercised this route's rendered HTML at all."""

from __future__ import annotations

import sqlite3
from datetime import timedelta, timezone
from datetime import datetime as dt

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from gateway.server import GatewayTools


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), spy


def _staged_run(conn, jira, level: str) -> str:
    tools = GatewayTools(jira=jira, conn=conn, level=level)
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")
    tools.propose_estimate_change(
        run_id, "TEST-1", new_points=3, reasoning="Reasoning long enough to pass validation.", assumptions="NA"
    )
    return run_id


def test_running_run_shows_a_still_working_note(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id = _staged_run(conn, jira, "L3")  # never finish_run'd - stays 'running'

    resp = test_client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    assert "still working through this task" in resp.text


def test_l3_awaiting_review_note_points_at_approve_or_send_back(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id = _staged_run(conn, jira, "L3")
    GatewayTools(jira=jira, conn=conn, level="L3").finish_run(run_id)

    resp = test_client.get(f"/runs/{run_id}")

    assert "Approve or send it back" in resp.text
    # The disclaimer above the staged-changes table only appears when there
    # is agent output to disclaim about.
    assert "written by an AI agent and may be wrong" in resp.text


def test_l4_awaiting_review_with_no_token_note_asks_to_acknowledge(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id = _staged_run(conn, jira, "L4")
    GatewayTools(jira=jira, conn=conn, level="L4").finish_run(run_id)

    resp = test_client.get(f"/runs/{run_id}")

    assert "acknowledge each change below" in resp.text


def test_l4_awaiting_review_with_active_token_note_says_not_yet(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id = _staged_run(conn, jira, "L4")
    GatewayTools(jira=jira, conn=conn, level="L4").finish_run(run_id)
    conn.execute(
        "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            "tok-1",
            run_id,
            dt.now(timezone.utc).isoformat(),
            (dt.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        ),
    )
    conn.commit()

    resp = test_client.get(f"/runs/{run_id}")

    assert "Jira updates once the agent spends it" in resp.text


def test_applied_run_note_says_record_is_closed(client, conn, jira_with_spy):
    test_client, spy = client
    jira, _ = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    run_id = _staged_run(conn, jira, "L4")
    GatewayTools(jira=jira, conn=conn, level="L4").finish_run(run_id)
    conn.execute(
        "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            "tok-2",
            run_id,
            dt.now(timezone.utc).isoformat(),
            (dt.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        ),
    )
    conn.commit()
    GatewayTools(jira=jira, conn=conn, level="L4").commit_changes(run_id, "tok-2")

    resp = test_client.get(f"/runs/{run_id}")

    assert "record is now closed" in resp.text


def test_rejected_run_note_says_nothing_reached_jira(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id = _staged_run(conn, jira, "L3")
    GatewayTools(jira=jira, conn=conn, level="L3").finish_run(run_id)
    test_client.post(f"/runs/{run_id}/reject", data={"comment": "not now"})

    resp = test_client.get(f"/runs/{run_id}")

    assert "nothing from this run reached Jira" in resp.text


def test_active_nav_link_is_marked_on_each_page(client):
    test_client, _ = client

    for path in ("/agent-console", "/chat", "/audit", "/approvals", "/settings", "/dashboard"):
        html = test_client.get(path).text
        assert html.count("nav-item active") == 1, path
        assert f'href="{path}" class="nav-item active"' in html, path


def test_footer_disclaimer_present_on_every_page(client):
    test_client, _ = client

    for path in ("/approvals", "/dashboard", "/agent-console", "/chat", "/audit", "/settings"):
        resp = test_client.get(path)
        assert "research prototype" in resp.text
        assert "Agents can make mistakes" in resp.text
