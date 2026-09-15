"""Prints the slice 2 demo: the L2 (human-edited value) and L4
(acknowledge-gated token, agent-consumed) stories, back to back, against
the real site. See docs/slice-2.md section 9.

For the L4 half, the human's part (acknowledge every row, click Issue
token in the browser) has to happen for real - this script can't click
buttons for you. Once a token exists, this script plays the agent's part
(calling commit_changes with it) exactly the way scripts/demo_slice1.py
plays the agent's part in slice 1 - there's still no orchestrator process,
Claude Code (or, here, this script) is still standing in for one.

Usage:
    uv run python scripts/demo_slice2.py [l2_run_id] [l4_run_id]

With no arguments, uses the most recent awaiting_review run at each level.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import get_connection  # noqa: E402
from gateway.jira import JiraClient  # noqa: E402
from gateway.server import GatewayTools  # noqa: E402

APP_URL = "http://localhost:8010"


def _find_run(conn: sqlite3.Connection, level: str, run_id: str | None) -> sqlite3.Row:
    if run_id:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise SystemExit(f"No run with id {run_id}")
        return row
    row = conn.execute(
        "SELECT * FROM runs WHERE level = ? AND status = 'awaiting_review' ORDER BY created_at DESC LIMIT 1",
        (level,),
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"No {level} run is awaiting_review. Propose one first (start_run / "
            f"propose_estimate_change / finish_run at {level}), or pass a run_id."
        )
    return row


def _print_points(client: JiraClient, issue_keys: list[str], heading: str) -> None:
    print(f"\n{heading}")
    for key in issue_keys:
        issue = client.get_issue(key)
        print(f"  {key}  {issue.summary}  ->  points={issue.story_points}")


def _print_proposals(changes: list[sqlite3.Row]) -> None:
    print("\nStaged proposals (agent's original values):")
    for c in changes:
        print(f"  {c['issue_key']}  {c['field']}: {c['old_value']} -> {c['new_value']}")
        print(f"      reasoning: {c['reasoning']}")
        print(f"      assumptions: {c['assumptions']}")


def _print_audit(conn: sqlite3.Connection, run_id: str) -> None:
    rows = conn.execute(
        "SELECT ts, actor, level, action, outcome, detail FROM audit_log "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    for r in rows:
        print(f"  {r['ts']}  {r['actor']:<16}  {r['action']:<16}  {r['outcome']:<10}  {r['detail'] or ''}")


def demo_l2(conn: sqlite3.Connection, client: JiraClient, run: sqlite3.Row) -> None:
    print("=" * 72)
    print(f"L2 DEMO - run {run['id']}")
    print("=" * 72)

    changes = conn.execute(
        "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run["id"],)
    ).fetchall()
    issue_keys = list(dict.fromkeys(c["issue_key"] for c in changes))

    _print_points(client, issue_keys, "1. Current state in Jira, before approval:")
    _print_proposals(changes)

    print(f"\n3. Go to {APP_URL}/runs/{run['id']}, change a value, and click Approve.")
    input("   Press Enter here once you've approved... ")

    _print_points(client, issue_keys, "4. State in Jira after your edit landed:")

    print("\n5. Audit log for this run (note which values were edited):")
    _print_audit(conn, run["id"])


def demo_l4(conn: sqlite3.Connection, client: JiraClient, run: sqlite3.Row) -> None:
    print("\n" + "=" * 72)
    print(f"L4 DEMO - run {run['id']}")
    print("=" * 72)

    changes = conn.execute(
        "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run["id"],)
    ).fetchall()
    issue_keys = list(dict.fromkeys(c["issue_key"] for c in changes))

    _print_points(client, issue_keys, "1. Current state in Jira, before commit:")
    _print_proposals(changes)

    print(f"\n3. Go to {APP_URL}/runs/{run['id']}, acknowledge every row, then click Issue token.")
    input("   Press Enter here once you've issued the token... ")

    token_row = conn.execute(
        "SELECT token FROM approval_tokens WHERE run_id = ? AND consumed_at IS NULL "
        "ORDER BY issued_at DESC LIMIT 1",
        (run["id"],),
    ).fetchone()
    if token_row is None:
        raise SystemExit("No unconsumed token found for this run - did Issue token actually succeed?")

    print(f"\n4. Agent calling commit_changes(run_id={run['id']!r}, approval_token=...)")
    tools = GatewayTools(jira=client, conn=conn, level="L4")
    result = tools.commit_changes(run["id"], token_row["token"])
    print(f"   -> {result}")

    _print_points(client, issue_keys, "5. State in Jira after commit:")

    print("\n6. Audit log for this run (note the two distinct actors - human issues, agent commits):")
    _print_audit(conn, run["id"])


def main() -> int:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    client = JiraClient()

    l2_run_id = sys.argv[1] if len(sys.argv) > 1 else None
    l4_run_id = sys.argv[2] if len(sys.argv) > 2 else None

    with client:
        demo_l2(conn, client, _find_run(conn, "L2", l2_run_id))
        demo_l4(conn, client, _find_run(conn, "L4", l4_run_id))

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
