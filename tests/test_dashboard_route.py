"""Route-level test for GET /dashboard - the Jira search is mocked (same
pattern as tests/test_app_l2_l4.py), the aggregation itself is already
covered against fixed data in tests/test_analytics.py."""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), spy


def _jira_issue(key, *, status_name="To Do", status_category="new", issue_type="Story", points=None, due=None):
    return {
        "key": key,
        "fields": {
            "summary": f"Summary for {key}",
            "status": {"name": status_name, "statusCategory": {"key": status_category}},
            "issuetype": {"name": issue_type},
            "duedate": due,
            "labels": [],
            "resolutiondate": None,
            "created": "2026-09-01T00:00:00.000+0000",
            "customfield_10016": points,
        },
    }


def test_dashboard_renders_stat_cards_and_charts(client):
    test_client, spy = client
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {
        "issues": [
            _jira_issue("MCP-1", status_category="done", points=3),
            _jira_issue("MCP-2", status_category="new", points=None),
        ]
    }

    resp = test_client.get("/dashboard")

    assert resp.status_code == 200
    assert "Unestimated stories" in resp.text
    assert "Pending approvals" in resp.text
    assert "Story points done" in resp.text
    assert "<svg" in resp.text
    assert "not yet estimated" in resp.text  # suggestion for the one unestimated story


def test_dashboard_shows_nothing_urgent_when_project_is_clean(client):
    test_client, spy = client
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": [_jira_issue("MCP-1", status_category="done", points=2)]}

    resp = test_client.get("/dashboard")

    assert resp.status_code == 200
    assert "Nothing urgent" in resp.text
