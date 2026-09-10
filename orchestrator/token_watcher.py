"""Watches for L4 approval tokens that have been issued but not yet
consumed, and spends them via a real MCP client call to commit_changes -
the automated counterpart to a human pasting a token into a Claude Code
chat and asking it to do the same.

This does not collapse L4 into L3's direct-apply. It's still two separate
actors: a human issues a token by acknowledging every staged change and
clicking "Issue token" in the browser; this process is the *agent* that
notices the token and spends it. commit_changes only exists as a tool at
L4, and it still only succeeds with a token the human's browser issued -
automating who's watching for that token doesn't change who's allowed to
use it. No LLM call is involved here: spending an already-authorised
token is mechanical, not a judgement call, so there is nothing for a
model to reason about at this step.

Runs as its own process, independent of the web app and of any specific
orchestrator run - `uv run python -m orchestrator.token_watcher [poll_seconds]`.
Nothing starts it automatically; a human runs it the same way they'd
choose to keep an interactive Claude Code session open.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

AGENT_IDENTITY = "orchestrator:token-watcher"
DEFAULT_POLL_SECONDS = 10.0


def find_due_tokens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unconsumed, unexpired tokens on L4 runs still awaiting_review - the
    only tokens anything other than the issuing app itself should ever
    try to spend. A run that's already applied (or a token that's expired
    or been consumed) naturally stops showing up here, so re-polling is
    always safe."""
    now = datetime.now(timezone.utc).isoformat()
    return conn.execute(
        "SELECT t.token, t.run_id FROM approval_tokens t "
        "JOIN runs r ON r.id = t.run_id "
        "WHERE t.consumed_at IS NULL AND t.expires_at > ? "
        "AND r.level = 'L4' AND r.status = 'awaiting_review'",
        (now,),
    ).fetchall()


def _error_text(result) -> str:
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return text
    return "commit_changes failed with no error detail"


async def commit_with_token(run_id: str, token: str) -> dict:
    """Spends one token by calling commit_changes through a real MCP
    client - a fresh gateway subprocess at L4, exactly like any other
    agent caller, not a backdoor into GatewayTools."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway.server"],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "AGENTIC_PM_LEVEL": "L4", "AGENTIC_PM_AGENT": AGENT_IDENTITY},
    )
    async with Client(server_params) as mcp_client:
        result = await mcp_client.call_tool("commit_changes", {"run_id": run_id, "approval_token": token})
        is_error = result.is_error
        payload = result.structured_content
    if is_error:
        raise RuntimeError(_error_text(result))
    return payload


async def watch_forever(poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    from db.migrate import get_connection

    conn = get_connection()
    conn.row_factory = sqlite3.Row
    print(f"Token watcher started as {AGENT_IDENTITY!r} - polling every {poll_seconds}s. Ctrl+C to stop.")
    while True:
        for row in find_due_tokens(conn):
            print(f"Found unconsumed token for run {row['run_id']} - committing...")
            try:
                result = await commit_with_token(row["run_id"], row["token"])
                print(f"  Applied: {result}")
            except Exception as exc:  # noqa: BLE001 - one bad run must not kill the watcher
                print(f"  Failed: {exc}")
        await asyncio.sleep(poll_seconds)


def main() -> int:
    poll_seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_POLL_SECONDS
    try:
        asyncio.run(watch_forever(poll_seconds))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
