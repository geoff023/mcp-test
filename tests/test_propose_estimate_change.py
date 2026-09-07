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
        confidence=0.6,
    )

    spy.request.assert_not_called()

    row = conn.execute(
        "SELECT run_id, issue_key, field, old_value, new_value, reasoning, confidence, applied_at "
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
        0.6,
        None,
    )


def test_propose_estimate_change_rejects_confidence_outside_unit_range(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")

    with pytest.raises(ValueError, match="confidence"):
        tools.propose_estimate_change(
            run_id, "TEST-1", new_points=3, reasoning="A reasoning string long enough to pass.", confidence=1.5
        )

    spy.request.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM staged_changes").fetchone()[0] == 0


def test_propose_estimate_change_rejects_short_reasoning(conn, jira_with_spy):
    jira, spy = jira_with_spy
    tools = GatewayTools(jira=jira, conn=conn, level="L3")
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")

    with pytest.raises(ValueError, match="reasoning"):
        tools.propose_estimate_change(run_id, "TEST-1", new_points=3, reasoning="too short", confidence=0.5)

    spy.request.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM staged_changes").fetchone()[0] == 0
