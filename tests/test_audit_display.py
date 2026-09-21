"""Tests for app/audit_display.py - the audit log's plain-language labels
and run-grouping, used by GET /audit (see tests/test_audit_route.py for
the route-level check)."""

from __future__ import annotations

import sqlite3

from app.audit_display import friendly_action, friendly_actor, friendly_detail, group_audit_rows, outcome_icon


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


def test_friendly_detail_drops_plumbing_the_reviewer_does_not_need():
    assert friendly_detail("chat_turn", "tool_calls=[\"get_issue({'issue_key': 'MCP-3'})\"]") is None
    assert friendly_detail("chat_turn", "no tool calls") is None
    assert friendly_detail("start_run", "task_type=reestimate scope=key in (MCP-2)") is None
    assert friendly_detail("issue_token", "token expires 2026-09-20; hand it to the agent to call commit_changes") is None
    assert friendly_detail("start_run", None) is None


def test_friendly_detail_rewrites_the_useful_details_in_plain_words():
    assert friendly_detail("propose_estimate_change", "MCP-2 -> 1.0 pts") == "MCP-2 set to 1.0 points"
    assert friendly_detail("finish_run", "1 staged changes") == "1 change staged"
    assert friendly_detail("finish_run", "9 staged changes") == "9 changes staged"
    assert friendly_detail("approve", "1 issue(s) applied: ['MCP-1']") == "Applied to MCP-1"
    assert friendly_detail("commit_changes", "2 issue(s): ['MCP-1', 'MCP-4']") == "Applied to MCP-1, MCP-4"
    assert friendly_detail("reject", "too high") == "Reason: too high"


def test_friendly_detail_explains_a_guardrail_refusal_without_python_reprs():
    detail = "refused due-date change on MCP-4: issue_type='Milestone', labels=['milestone']"
    text = friendly_detail("propose_due_date_change", detail)
    assert text == "MCP-4 is a milestone, so its due date can't be changed"


def _group(rows, run_id="run-1"):
    return {"run_id": run_id, "rows": rows}


def _row(**kw):
    base = {"action": "start_run", "outcome": "ok"}
    return {**base, **kw}


def test_group_kinds_classify_what_happened_to_a_run():
    from app.audit_display import group_kinds

    applied = _group([_row(action="approve", outcome="applied"), _row()])
    waiting = _group([_row(action="finish_run", outcome="awaiting_review"), _row()])
    sent_back = _group([_row(action="reject", outcome="rejected"), _row()])
    chat = _group([_row(action="chat_turn", outcome="answered")], run_id=None)

    assert group_kinds(applied) == {"changed"}
    assert group_kinds(waiting) == {"needs"}
    assert group_kinds(sent_back) == {"rejected"}
    assert group_kinds(chat) == {"advice"}


def test_filter_groups_and_counts_and_unknown_filter_falls_back_to_all():
    from app.audit_display import filter_counts, filter_groups

    groups = [
        _group([_row(action="approve", outcome="applied")]),
        _group([_row(action="finish_run", outcome="awaiting_review")], run_id="run-2"),
        _group([_row(action="chat_turn", outcome="answered")], run_id=None),
    ]

    assert len(filter_groups(groups, "all")) == 3
    assert len(filter_groups(groups, "advice")) == 1
    assert filter_groups(groups, "needs") == [groups[1]]
    assert len(filter_groups(groups, "nonsense")) == 3
    assert filter_counts(groups) == {"all": 3, "changed": 1, "needs": 1, "rejected": 0, "advice": 1}
