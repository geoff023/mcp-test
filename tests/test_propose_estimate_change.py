"""Test 1 from docs/slice-1.md: propose_estimate_change writes to the
staging table and makes no Jira call - asserted on the HTTP client itself,
not trusted.
"""

from __future__ import annotations

import pytest

from gateway.server import GatewayTools


def test_propose_estimate_change_writes_staging_row_and_calls_jira_zero_times(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")

    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")
    change_id = tools.propose_estimate_change(
        run_id,
        "TEST-1",
        new_points=3,
        reasoning="A sufficiently detailed reasoning string for validation purposes.",
        assumptions="Assumed the API and UI halves ship together.",
    )

    spy.request.assert_not_called()

    row = conn.execute(
        "SELECT run_id, issue_key, field, old_value, new_value, reasoning, assumptions, applied_at "
        "FROM staged_changes WHERE id = ?",
        (change_id,),
    ).fetchone()
    assert row == (
        run_id,
        "TEST-1",
        "story_points",
        None,
        "3",
        "A sufficiently detailed reasoning string for validation purposes.",
        "Assumed the API and UI halves ship together.",
        None,
    )


def test_propose_estimate_change_rejects_empty_assumptions(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")

    with pytest.raises(ValueError, match="assumptions"):
        tools.propose_estimate_change(
            run_id, "TEST-1", new_points=3, reasoning="A reasoning string long enough to pass.", assumptions="   "
        )

    spy.request.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM staged_changes").fetchone()[0] == 0


def test_propose_estimate_change_accepts_na_assumptions(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")

    change_id = tools.propose_estimate_change(
        run_id, "TEST-1", new_points=3, reasoning="A reasoning string long enough to pass.", assumptions="NA"
    )

    assumptions = conn.execute(
        "SELECT assumptions FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()[0]
    assert assumptions == "NA"


def test_propose_estimate_change_rejects_short_reasoning(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")

    with pytest.raises(ValueError, match="reasoning"):
        tools.propose_estimate_change(run_id, "TEST-1", new_points=3, reasoning="too short", assumptions="NA")

    spy.request.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM staged_changes").fetchone()[0] == 0
