"""Test 2 from docs/slice-1.md: commit_changes fails with a missing,
unknown, expired, consumed, or mismatched-run token - and never applies
anything to Jira when it does.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from gateway.server import GatewayTools


@pytest.fixture
def tools_l4(conn, jira_with_spy):
    jira, _ = jira_with_spy
    return GatewayTools(jira=jira, conn=conn, level="L4")


def _seed_run_with_change(tools: GatewayTools) -> str:
    run_id = tools.start_run(task_type="reestimate", scope="project = TEST")
    tools.propose_estimate_change(
        run_id, "TEST-1", new_points=5, reasoning="Reasoning long enough to pass validation.", assumptions="NA"
    )
    tools.finish_run(run_id)
    return run_id


def _insert_token(conn, *, run_id: str, expires_delta: timedelta, consumed: bool = False) -> str:
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at, consumed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            token,
            run_id,
            now.isoformat(),
            (now + expires_delta).isoformat(),
            now.isoformat() if consumed else None,
        ),
    )
    conn.commit()
    return token


def test_refuses_missing_token(conn, tools_l4, jira_with_spy):
    _, spy = jira_with_spy
    run_id = _seed_run_with_change(tools_l4)

    with pytest.raises(ValueError, match="missing or unknown"):
        tools_l4.commit_changes(run_id, "")

    spy.request.assert_not_called()


def test_refuses_unknown_token(conn, tools_l4, jira_with_spy):
    _, spy = jira_with_spy
    run_id = _seed_run_with_change(tools_l4)

    with pytest.raises(ValueError, match="missing or unknown"):
        tools_l4.commit_changes(run_id, str(uuid.uuid4()))

    spy.request.assert_not_called()


def test_refuses_expired_token(conn, tools_l4, jira_with_spy):
    _, spy = jira_with_spy
    run_id = _seed_run_with_change(tools_l4)
    token = _insert_token(conn, run_id=run_id, expires_delta=timedelta(minutes=-1))

    with pytest.raises(ValueError, match="expired"):
        tools_l4.commit_changes(run_id, token)

    spy.request.assert_not_called()


def test_refuses_consumed_token(conn, tools_l4, jira_with_spy):
    _, spy = jira_with_spy
    run_id = _seed_run_with_change(tools_l4)
    token = _insert_token(conn, run_id=run_id, expires_delta=timedelta(minutes=15), consumed=True)

    with pytest.raises(ValueError, match="already been consumed"):
        tools_l4.commit_changes(run_id, token)

    spy.request.assert_not_called()


def test_refuses_token_belonging_to_a_different_run(conn, tools_l4, jira_with_spy):
    _, spy = jira_with_spy
    run_id = _seed_run_with_change(tools_l4)
    other_run_id = _seed_run_with_change(tools_l4)
    token = _insert_token(conn, run_id=other_run_id, expires_delta=timedelta(minutes=15))

    with pytest.raises(ValueError, match="different run"):
        tools_l4.commit_changes(run_id, token)

    spy.request.assert_not_called()


def test_refusal_is_audited(conn, tools_l4):
    run_id = _seed_run_with_change(tools_l4)

    with pytest.raises(ValueError):
        tools_l4.commit_changes(run_id, "unknown-token")

    row = conn.execute(
        "SELECT action, outcome FROM audit_log WHERE run_id = ? AND action = 'commit_changes'", (run_id,)
    ).fetchone()
    assert row == ("commit_changes", "refused")


def test_valid_token_applies_and_consumes(conn, tools_l4, jira_with_spy):
    jira, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    run_id = _seed_run_with_change(tools_l4)
    token = _insert_token(conn, run_id=run_id, expires_delta=timedelta(minutes=15))

    result = tools_l4.commit_changes(run_id, token)

    assert result == {"run_id": run_id, "applied_issue_keys": ["TEST-1"]}
    consumed_at, run_status = conn.execute(
        "SELECT (SELECT consumed_at FROM approval_tokens WHERE token = ?), "
        "(SELECT status FROM runs WHERE id = ?)",
        (token, run_id),
    ).fetchone()
    assert consumed_at is not None
    assert run_status == "applied"
    # One PUT to update the story-points field - the only Jira call this test allows.
    spy.request.assert_called_once()
