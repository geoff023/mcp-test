"""Demonstrates slice 4's two behaviour changes against the real Jira site:

1. The assumptions field: runs a real reestimate task (same path as
   demo_slice3.py) and shows the agent's assumptions alongside its
   reasoning for each staged proposal - no numeric confidence anymore.
2. The L1 (Advisor) chat surface: asks the real Gemini-backed chat agent a
   read-only question, prints the tool calls it made and its answer, then
   re-reads the same issues from Jira to prove nothing changed - the
   before/after story a write-capable level would show is, for a read-only
   agent, "the state before and after is identical."

Usage:
    uv run python scripts/demo_slice4.py <target_jql> "<chat question>" [level]

level defaults to L2 for the reestimate half; the chat half always runs at L1.
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
from orchestrator.chat import ChatError, run_chat_turn  # noqa: E402


def _print_points(client: JiraClient, issue_keys: list[str], heading: str) -> None:
    print(f"\n{heading}")
    for key in issue_keys:
        issue = client.get_issue(key)
        print(f"  {key}  {issue.summary}  ->  points={issue.story_points}")


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit(f'Usage: {sys.argv[0]} <target_jql> "<chat question>" [level]')
    target_jql = sys.argv[1]
    chat_question = sys.argv[2]
    level = sys.argv[3] if len(sys.argv) > 3 else "L2"

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    client = JiraClient()

    print("=" * 72)
    print("SLICE 4 DEMO, PART 1 - assumptions field")
    print("=" * 72)
    with client:
        issue_keys = [i.key for i in client.search_issues(target_jql, max_results=50)]
        if not issue_keys:
            raise SystemExit(f"No issues match {target_jql!r} - nothing for the agent to estimate.")

        print(f"\n1. Handing off to Gemini at {level} via the MCP gateway (real, live call):")
        try:
            run_id = asyncio.run(run_reestimate_task(level=level, target_jql=target_jql, instructions=""))
        except OrchestratorError as exc:
            raise SystemExit(f"Orchestrator failed: {exc}") from exc
        print(f"   Agent finished. run_id={run_id}")

        changes = conn.execute(
            "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
        ).fetchall()
        print("\n2. Staged proposals - reasoning AND assumptions, no confidence percentage:")
        for c in changes:
            print(f"  {c['issue_key']}  {c['field']}: {c['old_value']} -> {c['new_value']}")
            print(f"      reasoning:   {c['reasoning']}")
            print(f"      assumptions: {c['assumptions']}")
        print(f"\n   Review this run at http://localhost:8000/runs/{run_id}")

    print("\n" + "=" * 72)
    print("SLICE 4 DEMO, PART 2 - L1 (Advisor) chat")
    print("=" * 72)
    with client:
        _print_points(client, issue_keys, "3. State in Jira BEFORE the chat turn:")

        print(f"\n4. Asking the L1 chat agent: {chat_question!r}")
        try:
            answer, tool_calls = asyncio.run(run_chat_turn(history=[], message=chat_question))
        except ChatError as exc:
            raise SystemExit(f"Chat turn failed: {exc}") from exc
        print(f"   Tool calls made ({len(tool_calls)}): {tool_calls}")
        print(f"   Answer: {answer}")

        _print_points(client, issue_keys, "5. State in Jira AFTER the chat turn (should be identical):")

    print("\nDone. The chat agent could only ever call search_issues/get_issue - see")
    print("TOOLS_BY_LEVEL['L1'] in gateway/server.py - so step 5 matching step 3 exactly")
    print("isn't luck, it's structural: there was never a tool it could use to change anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
