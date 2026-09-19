"""Tests for app/audit_display.py - the audit log's plain-language labels
and run-grouping, used by GET /audit (see tests/test_audit_route.py for
the route-level check)."""

from __future__ import annotations

import sqlite3

from app.audit_display import friendly_action, friendly_actor, group_audit_rows, outcome_icon


def test_friendly_action_maps_known_codes():
    assert friendly_action("propose_estimate_change") == "Proposed an estimate"
    assert friendly_action("commit_changes") == "Applied to Jira"


def test_friendly_action_falls_back_to_a_humanized_unknown_code():
    assert friendly_action("some_new_action") == "Some new action"


def test_friendly_actor_does_not_call_a_human_operator_an_agent():
    # agent:planning is a human typing into Claude Code by hand (see
    # gateway/server.py's DEFAULT_AGENT docstring) - the display name must
    # not claim it's an AI.
    assert friendly_actor("agent:planning") == "Human (via Claude Code)"


def test_friendly_actor_labels_the_real_llm_orchestrator_as_ai():
    assert friendly_actor("agent:orchestrator:gemini-3.6-flash") == "AI agent (gemini-3.6-flash)"


def test_friendly_actor_known_special_cases():
    assert friendly_actor("human:reviewer") == "You"
    assert friendly_actor("gateway") == "System"
    assert friendly_actor("agent:orchestrator:token-watcher") == "Token watcher (automated)"
    assert friendly_actor("agent:orchestrator:chat") == "Chat agent"


def test_friendly_actor_unknown_string_passes_through():
    assert friendly_actor("something:unexpected") == "something:unexpected"


def test_outcome_icon_by_category():
    assert outcome_icon("applied") == "✓"
    assert outcome_icon("rejected") == "✕"
    assert outcome_icon("awaiting_review") == "•"


def _rows(conn, entries):
    conn.execute(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, run_id TEXT, ts TEXT, actor TEXT, level TEXT, action TEXT, outcome TEXT, detail TEXT)"
    )
    for i, (run_id, action) in enumerate(entries):
        conn.execute(
            "INSERT INTO t (run_id, ts, actor, level, action, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, f"2026-09-19T00:00:{i:02d}", "human:reviewer", "L3", action, "ok", None),
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM t ORDER BY id DESC").fetchall()


def test_consecutive_same_run_rows_are_grouped_together():
    conn = sqlite3.connect(":memory:")
    rows = _rows(conn, [("run-1", "start_run"), ("run-1", "propose_estimate_change"), ("run-1", "finish_run")])

    groups = group_audit_rows(rows)

    assert len(groups) == 1
    assert groups[0]["run_id"] == "run-1"
    assert len(groups[0]["rows"]) == 3
    # first row in the group is the most recent action (id DESC input order preserved)
    assert groups[0]["rows"][0]["action"] == "finish_run"


def test_chat_turns_with_no_run_id_are_never_grouped_together():
    conn = sqlite3.connect(":memory:")
    rows = _rows(conn, [(None, "chat_turn"), (None, "chat_turn")])

    groups = group_audit_rows(rows)

    assert len(groups) == 2
    assert all(g["run_id"] is None and len(g["rows"]) == 1 for g in groups)


def test_different_runs_stay_in_separate_groups():
    # inserted in id order run-1, run-1, run-2; queried id DESC, so groups
    # come back newest-first: run-1's pair (ids 2,1), then run-2 (id 3).. -
    # actually DESC means id 3 (run-2) is newest and comes first.
    conn = sqlite3.connect(":memory:")
    rows = _rows(conn, [("run-1", "start_run"), ("run-1", "finish_run"), ("run-2", "start_run")])

    groups = group_audit_rows(rows)

    assert [g["run_id"] for g in groups] == ["run-2", "run-1"]
    assert len(groups[0]["rows"]) == 1
    assert len(groups[1]["rows"]) == 2
