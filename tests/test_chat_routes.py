"""Tests for app/main.py's /chat routes (slice 4). orchestrator/chat.py's
actual tool-use loop is stubbed here - tests/test_chat.py already covers
it against a real (in-process) L1 gateway.
"""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from orchestrator.chat import ChatError


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, spy = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app), spy


def test_chat_get_with_no_messages_shows_empty_state(client):
    test_client, _ = client

    resp = test_client.get("/chat")

    assert resp.status_code == 200
    assert "What should the agents advise on?" in resp.text


def test_chat_post_persists_both_turns_and_logs_audit(client, conn, monkeypatch):
    test_client, _ = client

    async def fake_run_chat_turn(*, history, message):
        assert history == []
        return "There are three unestimated stories.", ["search_issues({'jql': 'project = TEST'})"]

    monkeypatch.setattr(app_main, "run_chat_turn", fake_run_chat_turn)

    resp = test_client.post("/chat", data={"message": "Which stories have no estimate?"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/chat"

    rows = conn.execute("SELECT role, content FROM chat_messages ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("user", "Which stories have no estimate?"),
        ("agent", "There are three unestimated stories."),
    ]

    audit = conn.execute(
        "SELECT actor, level, action, outcome FROM audit_log WHERE action = 'chat_turn'"
    ).fetchone()
    assert tuple(audit) == ("agent:orchestrator:chat", "L1", "chat_turn", "answered")


def test_chat_post_survives_a_chat_error_and_still_records_the_exchange(client, conn, monkeypatch):
    test_client, _ = client

    async def failing_run_chat_turn(*, history, message):
        raise ChatError("did not produce a final answer within 10 tool calls")

    monkeypatch.setattr(app_main, "run_chat_turn", failing_run_chat_turn)

    resp = test_client.post("/chat", data={"message": "Anything"}, follow_redirects=False)

    assert resp.status_code == 303
    row = conn.execute("SELECT content FROM chat_messages WHERE role = 'agent'").fetchone()
    assert "couldn't answer" in row[0]

    outcome = conn.execute("SELECT outcome FROM audit_log WHERE action = 'chat_turn'").fetchone()[0]
    assert outcome == "failed"


def test_chat_post_honours_redirect_to_agent_console(client, monkeypatch):
    """The embedded chat step in agent_console.html sets redirect_to so
    sending a message reopens the console on the chat step instead of
    always landing on the standalone /chat page."""
    test_client, _ = client

    async def fake_run_chat_turn(*, history, message):
        return "answer", []

    monkeypatch.setattr(app_main, "run_chat_turn", fake_run_chat_turn)

    resp = test_client.post(
        "/chat",
        data={"message": "hi", "redirect_to": "/agent-console?level=L1"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/agent-console?level=L1"


def test_chat_post_rejects_an_unknown_redirect_to(client, monkeypatch):
    """redirect_to is form input, not a hardcoded template value - only a
    fixed allowlist of targets is honoured, anything else falls back to
    the safe default rather than being trusted as-is."""
    test_client, _ = client

    async def fake_run_chat_turn(*, history, message):
        return "answer", []

    monkeypatch.setattr(app_main, "run_chat_turn", fake_run_chat_turn)

    resp = test_client.post(
        "/chat",
        data={"message": "hi", "redirect_to": "https://evil.example/steal"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/chat"


def test_chat_get_shows_prior_messages_in_order(client, conn, monkeypatch):
    test_client, _ = client

    async def fake_run_chat_turn(*, history, message):
        return "answer one", []

    monkeypatch.setattr(app_main, "run_chat_turn", fake_run_chat_turn)
    test_client.post("/chat", data={"message": "question one"})

    resp = test_client.get("/chat")

    assert resp.status_code == 200
    assert resp.text.index("question one") < resp.text.index("answer one")
