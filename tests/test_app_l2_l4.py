"""Tests 1-3 from docs/slice-2.md section 10.

app/main.py holds its DB connection and Jira client as module-level
singletons (see its own docstring), so tests patch those two names on the
imported module rather than injecting them through the route functions -
the routes themselves are otherwise exercised exactly as a real request
would, via Starlette's TestClient over the real ASGI app.
"""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from gateway.server import GatewayTools


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row  # app/main.py's routes index rows by column name
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), spy


def _seed_run(conn, jira, level: str) -> tuple[str, str]:
    tools = GatewayTools(jira=jira, conn=conn, level=level)
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")
    tools.propose_estimate_change(
        run_id, "TEST-1", new_points=5, reasoning="Reasoning long enough to pass validation.", assumptions="NA"
    )
    tools.finish_run(run_id)
    change_id = conn.execute(
        "SELECT id FROM staged_changes WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    return run_id, change_id


# ---------- test 1: L2 applies edited_value, audit says so ----------


def test_l2_approve_applies_an_edited_value(client, conn, jira_with_spy):
    test_client, spy = client
    jira, _ = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    run_id, change_id = _seed_run(conn, jira, "L2")

    resp = test_client.post(f"/runs/{run_id}/approve", data={f"value_{change_id}": "2"})
    assert resp.status_code == 200

    new_value, edited_value = conn.execute(
        "SELECT new_value, edited_value FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()
    assert new_value == "5"  # untouched - always what the agent proposed
    assert edited_value == "2"  # what the human actually applied

    detail = conn.execute(
        "SELECT detail FROM audit_log WHERE run_id = ? AND action = 'approve'", (run_id,)
    ).fetchone()[0]
    assert "edited from the agent's proposal" in detail


def test_l2_approve_keeps_the_proposed_value_when_untouched(client, conn, jira_with_spy):
    test_client, spy = client
    jira, _ = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    run_id, change_id = _seed_run(conn, jira, "L2")

    resp = test_client.post(f"/runs/{run_id}/approve", data={f"value_{change_id}": "5"})
    assert resp.status_code == 200

    new_value, edited_value = conn.execute(
        "SELECT new_value, edited_value FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()
    assert new_value == "5"
    assert edited_value is None  # submitted value matched new_value - not an edit

    detail = conn.execute(
        "SELECT detail FROM audit_log WHERE run_id = ? AND action = 'approve'", (run_id,)
    ).fetchone()[0]
    assert "edited" not in detail


# ---------- test 2: /issue-token refuses until fully acknowledged ----------


def test_issue_token_refuses_while_unacknowledged(client, conn, jira_with_spy):
    test_client, spy = client
    jira, _ = jira_with_spy
    run_id, _ = _seed_run(conn, jira, "L4")

    resp = test_client.post(f"/runs/{run_id}/issue-token")

    assert resp.status_code == 400
    assert conn.execute(
        "SELECT COUNT(*) FROM approval_tokens WHERE run_id = ?", (run_id,)
    ).fetchone()[0] == 0
    spy.request.assert_not_called()


def test_issue_token_refuses_for_a_non_l4_run(client, conn, jira_with_spy):
    test_client, _ = client
    jira, _ = jira_with_spy
    run_id, _ = _seed_run(conn, jira, "L3")

    resp = test_client.post(f"/runs/{run_id}/issue-token")

    assert resp.status_code == 400


# ---------- test 3: full L4 round trip - issue then commit_changes ----------


def test_l4_issue_token_after_acknowledging_then_commit_changes_succeeds(client, conn, jira_with_spy):
    test_client, spy = client
    jira, _ = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    run_id, change_id = _seed_run(conn, jira, "L4")

    ack_resp = test_client.post(f"/runs/{run_id}/changes/{change_id}/acknowledge")
    assert ack_resp.status_code == 200
    assert conn.execute(
        "SELECT acknowledged FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()[0] == 1

    token_resp = test_client.post(f"/runs/{run_id}/issue-token")
    assert token_resp.status_code == 200

    token_row = conn.execute(
        "SELECT token FROM approval_tokens WHERE run_id = ? AND consumed_at IS NULL", (run_id,)
    ).fetchone()
    assert token_row is not None
    # Issuing the token applies nothing by itself.
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "awaiting_review"

    tools = GatewayTools(jira=jira, conn=conn, level="L4")
    result = tools.commit_changes(run_id, token_row[0])

    assert result == {"run_id": run_id, "applied_issue_keys": ["TEST-1"]}
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "applied"
