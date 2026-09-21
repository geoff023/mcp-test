"""Tests for the v7-prototype app shell and the pages built on it: the sidebar
(nav, Approvals badge, Jira card), the redirect from /, the Approvals queue,
Activity filters, the read-only Settings page, and the dashboard tolerating
Jira being unreachable now that it is the landing page."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from db.audit import log_audit


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), conn


def _insert_run(conn, run_id, status, level="L3", minutes_ago=5, scope="key in (MCP-3)"):
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO runs (id, level, task_type, agent, scope, created_at, status) VALUES (?, ?, 'reestimate', 'planning', ?, ?, ?)",
        (run_id, level, scope, created, status),
    )
    conn.commit()


def test_root_lands_on_the_dashboard_like_the_prototypes_first_nav_item(client):
    test_client, _ = client

    resp = test_client.get("/", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "/dashboard"


def test_sidebar_has_every_tab_including_chat_and_groups_them(client):
    test_client, _ = client

    html = test_client.get("/approvals").text

    for href in ("/dashboard", "/agent-console", "/chat", "/approvals", "/audit", "/settings"):
        assert f'href="{href}"' in html
    assert html.index("AGENTS") < html.index('href="/chat"') < html.index("CONFIGURATION")


def test_sidebar_badge_counts_runs_waiting_on_a_person_and_hides_at_zero(client):
    test_client, conn = client
    assert "nav-badge" not in test_client.get("/approvals").text

    _insert_run(conn, "r1", "awaiting_review")
    _insert_run(conn, "r2", "awaiting_review", level="L2")
    _insert_run(conn, "r3", "applied")

    html = test_client.get("/audit").text  # any page carries the badge
    assert '<span class="nav-badge" aria-label="2 waiting">2</span>' in html


def test_sidebar_and_top_bar_show_the_jira_project_and_link_out(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        app_main, "_jira", SimpleNamespace(config=SimpleNamespace(base_url="https://acme.atlassian.net/", project_key="ACME"))
    )

    html = test_client.get("/approvals").text

    assert "Jira &middot; ACME" in html
    assert "acme.atlassian.net" in html
    assert 'href="https://acme.atlassian.net/browse/ACME"' in html
    assert "Open in Jira" in html


def test_approvals_queue_lists_waiting_runs_oldest_first_then_earlier_runs(client):
    test_client, conn = client
    _insert_run(conn, "newer", "awaiting_review", level="L2", minutes_ago=5, scope="key in (MCP-4)")
    _insert_run(conn, "older", "awaiting_review", level="L4", minutes_ago=120, scope="key in (MCP-3)")
    _insert_run(conn, "done", "applied", scope="key in (MCP-1)")

    html = test_client.get("/approvals").text

    assert "2 waiting for you" in html
    assert html.index('href="/runs/older"') < html.index('href="/runs/newer"')  # oldest first
    assert "Ready 2 h ago" in html and "Ready 5 min ago" in html
    assert "Issue MCP-3" in html and "Super-Pilot" in html
    assert "Earlier runs" in html and 'href="/runs/done"' in html
    assert html.index("Earlier runs") < html.index('href="/runs/done"')


def test_activity_filters_narrow_the_list_and_ignore_unknown_values(client):
    test_client, conn = client
    log_audit(conn, run_id=None, actor="agent:orchestrator:chat", level="L1", action="chat_turn", outcome="answered")
    log_audit(conn, run_id="run-1", actor="agent:planning", level="L3", action="start_run", outcome="ok")
    log_audit(conn, run_id="run-1", actor="agent:planning", level="L3", action="finish_run", outcome="awaiting_review")

    everything = test_client.get("/audit").text
    advice = test_client.get("/audit?show=advice").text
    needs = test_client.get("/audit?show=needs").text
    changed = test_client.get("/audit?show=changed").text
    bogus = test_client.get("/audit?show=nonsense").text

    assert "Answered a question" in everything and "Finished staging" in everything
    assert "Answered a question" in advice and "Finished staging" not in advice
    assert "Finished staging" in needs and "Answered a question" not in needs
    assert "Nothing matches this filter" in changed
    assert "Needs you &middot; 1" in everything
    assert "Answered a question" in bogus and "Finished staging" in bogus


def test_settings_shows_exactly_the_tools_the_gateway_gives_each_level(client):
    """Read straight from TOOLS_BY_LEVEL: Consultant has no write tools at all,
    only Super-Pilot can apply changes to Jira."""
    from gateway.server import TOOLS_BY_LEVEL

    test_client, _ = client
    html = test_client.get("/settings").text
    rows = html.split("<tbody>")[1].split("</tbody>")[0].split("<tr>")[1:5]  # the four mode rows

    consultant, coworker, committer, superpilot = rows
    assert "Propose an estimate" not in consultant and "Apply changes to Jira" not in consultant
    assert "Propose an estimate" in coworker and "Apply changes to Jira" not in coworker
    assert "Apply changes to Jira" not in committer
    assert "Apply changes to Jira" in superpilot
    assert "commit_changes" in TOOLS_BY_LEVEL["L4"] and "commit_changes" not in TOOLS_BY_LEVEL["L3"]
    assert "Milestone due dates" in html


def test_dashboard_still_loads_when_jira_is_unreachable(client, monkeypatch):
    test_client, conn = client
    _insert_run(conn, "r1", "awaiting_review")

    def down(jql):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(app_main._jira, "search_issues_for_analytics", down)

    resp = test_client.get("/dashboard")

    assert resp.status_code == 200
    assert "Couldn&#39;t reach Jira" in resp.text or "Couldn't reach Jira" in resp.text
    assert "Pending approvals" in resp.text  # Orbit's own numbers still render


def test_dashboard_agent_runs_card_splits_runs_by_mode(client, jira_with_spy):
    test_client, conn = client
    _, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}
    _insert_run(conn, "a", "applied", level="L2")
    _insert_run(conn, "b", "applied", level="L2")
    _insert_run(conn, "c", "awaiting_review", level="L4")

    html = test_client.get("/dashboard").text

    assert "Co-worker 2" in html and "Super-Pilot 1" in html
    assert "Across 1 autonomy level" in html
