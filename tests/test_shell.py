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


def test_settings_shows_exactly_the_tools_the_gateway_gives_each_mode(client):
    """Read straight from TOOLS_BY_LEVEL: Co-Pilot has no write tools at all,
    only Auto-Pilot can apply changes to Jira. One row per product mode
    (M1/M2/M3) - L3 tool-identical to L2, so it isn't a separate row here
    (see show_settings's docstring)."""
    from gateway.server import TOOLS_BY_LEVEL

    test_client, _ = client
    html = test_client.get("/settings").text
    rows = html.split("<tbody>")[1].split("</tbody>")[0].split("<tr>")[1:4]  # the three mode rows

    copilot, superpilot, autopilot = rows
    assert "Propose an estimate" not in copilot and "Apply changes to Jira" not in copilot
    assert "Propose an estimate" in superpilot and "Apply changes to Jira" not in superpilot
    assert "Apply changes to Jira" in autopilot
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

    assert "Super-Pilot 2" in html and "Auto-Pilot 1" in html
    assert "Across 1 mode" in html


def test_dashboard_agent_runs_card_merges_l2_and_l3_into_one_super_pilot_bucket(client, jira_with_spy):
    """M2 (Super-Pilot) display-merges L2 and L3 - see app/levels.py's
    module docstring - so a mix of the two sums into one bucket, not two."""
    test_client, conn = client
    _, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}
    _insert_run(conn, "a", "applied", level="L2")
    _insert_run(conn, "b", "applied", level="L3")

    html = test_client.get("/dashboard").text

    assert "Super-Pilot 2" in html
    assert "Committer" not in html and "Co-worker" not in html


def test_sidebar_has_the_three_mode_entry_points(client):
    """Super-Pilot and Auto-Pilot are separate pages again - Auto-Pilot's
    own configure/active-automations split (autopilot.html) doesn't fit
    as a dropdown on Super-Pilot's wizard."""
    test_client, _ = client

    html = test_client.get("/approvals").text

    assert "Co-Pilot" in html and "Super-Pilot" in html and "Auto-Pilot" in html
    assert 'href="/agent-console"' in html
    assert 'href="/autopilot"' in html


def test_autopilot_page_has_configure_and_active_automations_tabs(client):
    test_client, _ = client

    html = test_client.get("/autopilot").text

    assert 'data-tab-btn="automations"' in html
    assert 'data-tab-btn="configure"' in html
    assert "Automatically update all tasks with no story points" in html


def test_autopilot_seeded_automation_has_a_real_run_now_action(client):
    """The toggle/delete/automation-card chrome is client-side only (no
    automations table exists), but Run now must still POST to the same
    tested /agent-console/start path Super-Pilot uses - that part is real
    for the reestimate automation. The rest of the default list is
    placeholder data (see _seeded_automations) with Run now disabled."""
    test_client, _ = client

    html = test_client.get("/autopilot").text

    assert 'data-task-type="reestimate" data-scope="unestimated" data-available="true"' in html
    assert "Run now" in html
    assert "/agent-console/start" in html
    assert "Not built yet" in html  # at least one placeholder automation


def test_autopilot_placeholder_automations_have_run_now_disabled(client):
    test_client, _ = client

    html = test_client.get("/autopilot").text

    assert 'data-task-type="forecast_watch"' in html
    card_html = html[html.index('data-task-type="forecast_watch"'):]
    run_btn = card_html[card_html.index('automation-run'):card_html.index('automation-run') + 60]
    assert "disabled" in run_btn


def test_approvals_card_has_a_quick_reject_that_redirects_back_to_the_list(client, conn):
    test_client, _ = client
    _insert_run(conn, "r1", "awaiting_review")

    html = test_client.get("/approvals").text
    assert 'action="/runs/r1/reject"' in html
    assert 'name="redirect_to" value="/approvals"' in html

    resp = test_client.post("/runs/r1/reject", data={"redirect_to": "/approvals"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/approvals"
    assert conn.execute("SELECT status FROM runs WHERE id = 'r1'").fetchone()[0] == "rejected"


def test_quick_reject_removes_the_run_from_approvals_and_logs_to_activity(client, conn):
    test_client, _ = client
    _insert_run(conn, "r1", "awaiting_review")

    test_client.post("/runs/r1/reject", data={"redirect_to": "/approvals"})

    approvals_html = test_client.get("/approvals").text
    assert "0 waiting for you" in approvals_html
    assert 'href="/runs/r1"' not in approvals_html.split("Earlier runs")[0]

    activity_html = test_client.get("/audit").text
    assert "Sent back" in activity_html


def test_reject_without_redirect_to_still_lands_on_the_run_page(client, conn):
    """The run page's own Send back form doesn't set redirect_to - must keep
    working exactly as before this button was added."""
    test_client, _ = client
    _insert_run(conn, "r1", "awaiting_review")

    resp = test_client.post("/runs/r1/reject", data={"comment": "not now"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/runs/r1"


def test_reject_ignores_an_unrecognised_redirect_to(client, conn):
    test_client, _ = client
    _insert_run(conn, "r1", "awaiting_review")

    resp = test_client.post("/runs/r1/reject", data={"redirect_to": "https://evil.example/"}, follow_redirects=False)

    assert resp.headers["location"] == "/runs/r1"
