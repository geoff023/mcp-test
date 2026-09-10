"""Runs the orchestrator for real against the live site, then narrates the
same before/after story as demo_slice1.py/demo_slice2.py.

The difference from those two: the "staged proposals" section isn't
pre-seeded by a script or a human standing in for the agent - it's
whatever Gemini actually decided to propose, for real, when it ran.

Usage:
    uv run python scripts/demo_slice3.py <target_jql> [level] [instructions]

level defaults to L3. instructions defaults to empty.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import get_connection  # noqa: E402
from gateway.jira import JiraClient  # noqa: E402
from orchestrator.agent import OrchestratorError, run_reestimate_task  # noqa: E402

APP_URL = "http://localhost:8010"


def _print_points(client: JiraClient, issue_keys: list[str], heading: str) -> None:
    print(f"\n{heading}")
    for key in issue_keys:
        issue = client.get_issue(key)
        print(f"  {key}  {issue.summary}  ->  points={issue.story_points}")


def _print_audit(conn: sqlite3.Connection, run_id: str) -> None:
    rows = conn.execute(
        "SELECT ts, actor, level, action, outcome, detail FROM audit_log "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    for r in rows:
        print(f"  {r['ts']}  {r['actor']:<28}  {r['action']:<24}  {r['outcome']:<10}  {r['detail'] or ''}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <target_jql> [level] [instructions]")
    target_jql = sys.argv[1]
    level = sys.argv[2] if len(sys.argv) > 2 else "L3"
    instructions = sys.argv[3] if len(sys.argv) > 3 else ""

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    client = JiraClient()

    print("=" * 72)
    print(f"SLICE 3 DEMO - orchestrator run at {level}, target: {target_jql}")
    print("=" * 72)

    with client:
        issue_keys = [i.key for i in client.search_issues(target_jql, max_results=50)]
        if not issue_keys:
            raise SystemExit(f"No issues match {target_jql!r} - nothing for the agent to estimate.")

        _print_points(client, issue_keys, "1. Current state in Jira, before the agent runs:")

        print("\n2. Handing off to Gemini via the MCP gateway - this is a real, live call:")
        try:
            run_id = asyncio.run(run_reestimate_task(level=level, target_jql=target_jql, instructions=instructions))
        except OrchestratorError as exc:
            raise SystemExit(f"Orchestrator failed: {exc}") from exc
        print(f"   Agent finished. run_id={run_id}")

        changes = conn.execute(
            "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
        ).fetchall()
        print("\n3. Staged proposals (Gemini's own reasoning - nothing pre-scripted):")
        for c in changes:
            print(
                f"  {c['issue_key']}  {c['field']}: {c['old_value']} -> {c['new_value']}"
                f"  (confidence={c['confidence']:.2f})"
            )
            print(f"      reasoning: {c['reasoning']}")

        print(f"\n4. Go review (and approve or send back) this run in the browser:")
        print(f"   {APP_URL}/runs/{run_id}")
        input("   Press Enter here once you've decided... ")

        _print_points(client, issue_keys, "5. State in Jira after your decision:")

    print("\n6. Audit log for this run:")
    _print_audit(conn, run_id)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
