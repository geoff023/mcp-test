"""Prints the full before/after story for one run: current Jira state, the
agent's staged proposals, a pause for human approval in the browser, Jira
state again showing the change landed, and the run's audit trail.

This is the slice 1 demo and its evidence - see docs/slice-1.md.

Usage:
    uv run python scripts/demo_slice1.py [run_id]

With no run_id, uses the most recent run still awaiting_review.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import get_connection  # noqa: E402
from gateway.jira import JiraClient  # noqa: E402

APP_URL = "http://localhost:8000"


def _find_run(conn: sqlite3.Connection, run_id: str | None) -> sqlite3.Row:
    if run_id:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise SystemExit(f"No run with id {run_id}")
        return row
    row = conn.execute(
        "SELECT * FROM runs WHERE status = 'awaiting_review' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SystemExit(
            "No run is awaiting_review. Ask the agent to propose some changes first "
            "(start_run / propose_estimate_change / finish_run via the MCP gateway), "
            "or pass a run_id explicitly."
        )
    return row


def _print_points(client: JiraClient, issue_keys: list[str], heading: str) -> None:
    print(f"\n{heading}")
    for key in issue_keys:
        issue = client.get_issue(key)
        print(f"  {key}  {issue.summary}  ->  points={issue.story_points}")


def main() -> int:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    client = JiraClient()

    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    run = _find_run(conn, run_id)
    run_id = run["id"]

    changes = conn.execute(
        "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
    ).fetchall()
    if not changes:
        raise SystemExit(f"Run {run_id} has no staged changes.")
    issue_keys = list(dict.fromkeys(c["issue_key"] for c in changes))

    print("=" * 72)
    print(f"SLICE 1 DEMO - run {run_id}  (level={run['level']}, task_type={run['task_type']})")
    print("=" * 72)

    with client:
        # 1. Current story points for the target issues, read from Jira.
        _print_points(client, issue_keys, "1. Current state in Jira, before approval:")

        # 2. The staged proposals from the DB, with reasoning and assumptions.
        print("\n2. Staged proposals (from the local DB - never yet touched Jira):")
        for c in changes:
            print(f"  {c['issue_key']}  {c['field']}: {c['old_value']} -> {c['new_value']}")
            print(f"      reasoning: {c['reasoning']}")
            print(f"      assumptions: {c['assumptions']}")

        # 3. A pause telling the operator to approve in the browser.
        print("\n3. Go approve (or send back) this run in the browser:")
        print(f"   {APP_URL}/runs/{run_id}")
        input("   Press Enter here once you've decided... ")

        # 4. The story points read from Jira again, showing the change landed.
        _print_points(client, issue_keys, "4. State in Jira after your decision:")

    # 5. The audit log rows for the run.
    print("\n5. Audit log for this run:")
    rows = conn.execute(
        "SELECT ts, actor, level, action, outcome, detail FROM audit_log "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    for r in rows:
        print(f"  {r['ts']}  {r['actor']:<16}  {r['action']:<24}  {r['outcome']:<10}  {r['detail'] or ''}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
