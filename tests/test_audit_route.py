"""Route-level test for GET /audit's humanized, grouped rendering (the
label/grouping logic itself is covered in tests/test_audit_display.py)."""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from db.audit import log_audit


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app)


def test_multi_step_run_renders_as_a_collapsible_group_with_friendly_labels(client, conn):
    log_audit(conn, run_id="run-1", actor="agent:planning", level="L3", action="start_run", outcome="ok")
    log_audit(conn, run_id="run-1", actor="agent:planning", level="L3", action="propose_estimate_change", outcome="staged")
    log_audit(conn, run_id="run-1", actor="human:reviewer", level="L3", action="approve", outcome="applied")

    resp = client.get("/audit")

    assert resp.status_code == 200
    assert "<details" in resp.text
    assert "Applied" in resp.text or "Approved" in resp.text
    assert "Human (via Claude Code)" in resp.text
    assert "You" in resp.text
    # raw action codes should never leak through unhumanized
    assert "propose_estimate_change" not in resp.text
    assert "start_run" not in resp.text


def test_single_action_run_renders_without_a_details_wrapper(client, conn):
    log_audit(conn, run_id=None, actor="agent:orchestrator:chat", level="L1", action="chat_turn", outcome="answered")

    resp = client.get("/audit")

    assert resp.status_code == 200
    assert "<details" not in resp.text
    assert "Chat agent" in resp.text
    assert "Answered a question" in resp.text


def test_empty_audit_log_shows_the_empty_state(client):
    resp = client.get("/audit")

    assert resp.status_code == 200
    assert "Nothing logged yet" in resp.text
