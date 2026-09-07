"""Test 4 from docs/slice-1.md - the one that proves the thesis: at L3 the
server's advertised tool list does not contain commit_changes. Autonomy is
enforced by which tools exist, not by asking the model to behave.
"""

from __future__ import annotations

import asyncio

from gateway.server import build_server

READ_TOOLS = {"search_issues", "get_issue"}
PROPOSE_TOOLS = {"start_run", "propose_estimate_change", "propose_due_date_change", "finish_run"}


def _tool_names(conn, jira, level: str) -> set[str]:
    server = build_server(level, jira=jira, conn=conn)
    tools = asyncio.run(server.list_tools())
    return {t.name for t in tools}


def test_l1_registers_only_read_tools(conn, jira_with_spy):
    jira, _ = jira_with_spy
    names = _tool_names(conn, jira, "L1")

    assert names == READ_TOOLS
    # Slice 2 regression guard: L1 still gets no write surface at all, not
    # even a soft one - see docs/slice-2.md's L1 decision.
    assert "start_run" not in names


def test_l2_registers_read_and_propose_tools(conn, jira_with_spy):
    jira, _ = jira_with_spy
    assert _tool_names(conn, jira, "L2") == READ_TOOLS | PROPOSE_TOOLS


def test_l3_tool_list_does_not_contain_commit_changes(conn, jira_with_spy):
    jira, _ = jira_with_spy
    names = _tool_names(conn, jira, "L3")

    assert names == READ_TOOLS | PROPOSE_TOOLS
    assert "commit_changes" not in names


def test_l4_adds_commit_changes(conn, jira_with_spy):
    jira, _ = jira_with_spy
    names = _tool_names(conn, jira, "L4")

    assert names == READ_TOOLS | PROPOSE_TOOLS | {"commit_changes"}


def test_unknown_level_is_rejected(conn, jira_with_spy):
    jira, _ = jira_with_spy
    try:
        build_server("L5", jira=jira, conn=conn)
    except ValueError as exc:
        assert "L5" in str(exc)
    else:
        raise AssertionError("build_server('L5') should have raised ValueError")
