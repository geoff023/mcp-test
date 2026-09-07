"""MCP gateway server.

Registers a different set of tools depending on the autonomy level (see
CLAUDE.md section 2). This is the entire enforcement mechanism: a level
that shouldn't be able to write to Jira simply never receives a
`commit_changes` tool - the model is never asked to behave, the tool does
not exist for it to call.

Reads AGENTIC_PM_LEVEL from the environment at startup (default L3).
Nothing here prints to stdout - stdio is the MCP transport, and a stray
print() corrupts the protocol and silently breaks the connection.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from db.audit import log_audit
from db.migrate import get_connection
from gateway.apply import apply_staged_changes
from gateway.guardrails import check_no_milestone_date_change
from gateway.jira import Issue, JiraClient

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

AGENT_NAME = "planning"
ACTOR = f"agent:{AGENT_NAME}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_summary(issue: Issue) -> dict:
    return {
        "key": issue.key,
        "summary": issue.summary,
        "status": issue.status,
        "issue_type": issue.issue_type,
        "story_points": issue.story_points,
        "due_date": issue.due_date,
        "labels": issue.labels,
    }


def _validate_proposal(reasoning: str, confidence: float) -> None:
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be between 0.0 and 1.0")
    if len(reasoning) < 20:
        raise ValueError(
            "reasoning must be at least 20 characters - an unexplained proposal is not a proposal"
        )


class GatewayTools:
    """The tool implementations, holding the shared Jira client and DB
    connection every tool needs. A plain class rather than module-level
    functions so tests can inject a fake JiraClient/connection instead of
    hitting the network or a real database file.
    """

    def __init__(self, jira: JiraClient, conn: sqlite3.Connection, level: str):
        self.jira = jira
        self.conn = conn
        self.level = level
        self._lock = threading.Lock()

    # ---------- always registered (read) ----------

    def search_issues(self, jql: str, max_results: int = 50) -> list[dict]:
        """Search Jira issues by JQL. Returns key, summary, status, story
        points, issue type, due date, and labels for each match."""
        with self._lock:
            issues = self.jira.search_issues(jql, max_results=max_results)
        return [_issue_summary(i) for i in issues]

    def get_issue(self, issue_key: str) -> dict:
        """Full detail for one issue, including its description as plain text."""
        with self._lock:
            issue = self.jira.get_issue(issue_key)
        return _issue_summary(issue) | {"description": issue.description}

    # ---------- registered at L2, L3, L4 ----------

    def start_run(self, task_type: str, scope: str) -> str:
        """Open a run. Every proposal must belong to one. Returns the run_id."""
        run_id = str(uuid.uuid4())
        with self._lock:
            self.conn.execute(
                "INSERT INTO runs (id, level, task_type, agent, scope, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running')",
                (run_id, self.level, task_type, AGENT_NAME, scope, _now()),
            )
            self.conn.commit()
            log_audit(
                self.conn,
                run_id=run_id,
                actor=ACTOR,
                level=self.level,
                action="start_run",
                outcome="ok",
                detail=f"task_type={task_type} scope={scope}",
            )
        return run_id

    def propose_estimate_change(
        self, run_id: str, issue_key: str, new_points: float, reasoning: str, confidence: float
    ) -> str:
        """Stage a story-point re-estimate for one issue. Writes only to the
        staging table - never touches Jira - so the proposal cannot be
        applied until a human approves it. Rejects confidence outside 0-1
        and reasoning shorter than 20 characters. Returns the staged_change_id.
        """
        _validate_proposal(reasoning, confidence)
        change_id = str(uuid.uuid4())
        with self._lock:
            # old_value is left NULL deliberately: fetching it would mean an
            # extra Jira call from a tool that must make none (see
            # tests/test_propose_estimate_change.py). The agent already knows
            # the current value from search_issues/get_issue; the review
            # surface fetches the live value again when it renders.
            self.conn.execute(
                "INSERT INTO staged_changes "
                "(id, run_id, issue_key, field, old_value, new_value, reasoning, confidence) "
                "VALUES (?, ?, ?, 'story_points', NULL, ?, ?, ?)",
                (change_id, run_id, issue_key, str(new_points), reasoning, confidence),
            )
            self.conn.commit()
            log_audit(
                self.conn,
                run_id=run_id,
                actor=ACTOR,
                level=self.level,
                action="propose_estimate_change",
                outcome="staged",
                detail=f"{issue_key} -> {new_points} pts",
            )
        return change_id

    def propose_due_date_change(
        self, run_id: str, issue_key: str, new_due_date: str, reasoning: str, confidence: float
    ) -> str:
        """Stage a due-date change for one issue. Slice 1 has no date-changing
        UI yet - this tool exists solely to demonstrate the
        no_milestone_date_change guardrail: any issue that is an Epic or
        carries the 'milestone' label is refused here, at every level.
        """
        _validate_proposal(reasoning, confidence)
        with self._lock:
            issue = self.jira.get_issue(issue_key)
            check_no_milestone_date_change(
                self.conn, issue, run_id=run_id, actor=ACTOR, level=self.level
            )
            change_id = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO staged_changes "
                "(id, run_id, issue_key, field, old_value, new_value, reasoning, confidence) "
                "VALUES (?, ?, ?, 'due_date', ?, ?, ?, ?)",
                (change_id, run_id, issue_key, issue.due_date, new_due_date, reasoning, confidence),
            )
            self.conn.commit()
            log_audit(
                self.conn,
                run_id=run_id,
                actor=ACTOR,
                level=self.level,
                action="propose_due_date_change",
                outcome="staged",
                detail=f"{issue_key}: {issue.due_date} -> {new_due_date}",
            )
        return change_id

    def finish_run(self, run_id: str) -> dict:
        """Mark a run awaiting_review and return a summary for the human."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT issue_key, field, new_value, confidence FROM staged_changes WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            self.conn.execute("UPDATE runs SET status = 'awaiting_review' WHERE id = ?", (run_id,))
            self.conn.commit()
            log_audit(
                self.conn,
                run_id=run_id,
                actor=ACTOR,
                level=self.level,
                action="finish_run",
                outcome="awaiting_review",
                detail=f"{len(rows)} staged changes",
            )
        return {
            "run_id": run_id,
            "status": "awaiting_review",
            "staged_changes": [
                {"issue_key": k, "field": f, "new_value": v, "confidence": c} for k, f, v, c in rows
            ],
        }

    # ---------- registered at L4 only ----------

    def commit_changes(self, run_id: str, approval_token: str) -> dict:
        """Apply a run's staged changes to Jira. Requires an approval_token
        issued by the human's browser. Fails loudly - and applies nothing -
        if the token is missing, unknown, expired, already consumed, or
        belongs to a different run.
        """
        with self._lock:
            token_row = self.conn.execute(
                "SELECT run_id, expires_at, consumed_at FROM approval_tokens WHERE token = ?",
                (approval_token,),
            ).fetchone()
            self._require_valid_token(token_row, run_id)

            applied = apply_staged_changes(self.jira, self.conn, run_id, actor=ACTOR, level=self.level)

            self.conn.execute(
                "UPDATE approval_tokens SET consumed_at = ? WHERE token = ?", (_now(), approval_token)
            )
            self.conn.execute("UPDATE runs SET status = 'applied' WHERE id = ?", (run_id,))
            self.conn.commit()
            applied_keys = [a["issue_key"] for a in applied]
            log_audit(
                self.conn,
                run_id=run_id,
                actor=ACTOR,
                level=self.level,
                action="commit_changes",
                outcome="applied",
                detail=f"{len(applied)} issue(s): {applied_keys}",
            )
        return {"run_id": run_id, "applied_issue_keys": applied_keys}

    def _require_valid_token(self, token_row: tuple | None, run_id: str) -> None:
        if token_row is None:
            self._refuse_commit(run_id, "approval_token is missing or unknown")
            return
        token_run_id, expires_at, consumed_at = token_row
        if token_run_id != run_id:
            self._refuse_commit(run_id, "approval_token belongs to a different run")
        if consumed_at is not None:
            self._refuse_commit(run_id, "approval_token has already been consumed")
        if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
            self._refuse_commit(run_id, "approval_token has expired")

    def _refuse_commit(self, run_id: str, reason: str) -> None:
        log_audit(
            self.conn,
            run_id=run_id,
            actor=ACTOR,
            level=self.level,
            action="commit_changes",
            outcome="refused",
            detail=reason,
        )
        self.conn.commit()
        raise ValueError(reason)


READ_TOOL_NAMES = ("search_issues", "get_issue")
PROPOSE_TOOL_NAMES = ("start_run", "propose_estimate_change", "propose_due_date_change", "finish_run")
COMMIT_TOOL_NAMES = ("commit_changes",)

# The whole enforcement mechanism lives in this one table: adding a level
# is listing tool names here, never restructuring the tools themselves.
TOOLS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "L1": READ_TOOL_NAMES,
    "L2": READ_TOOL_NAMES + PROPOSE_TOOL_NAMES,
    "L3": READ_TOOL_NAMES + PROPOSE_TOOL_NAMES,
    "L4": READ_TOOL_NAMES + PROPOSE_TOOL_NAMES + COMMIT_TOOL_NAMES,
}


def build_server(
    level: str,
    jira: JiraClient | None = None,
    conn: sqlite3.Connection | None = None,
) -> MCPServer:
    """Build the MCP server with exactly the tools `level` is allowed to see."""
    if level not in TOOLS_BY_LEVEL:
        raise ValueError(f"Unknown level {level!r}; must be one of {sorted(TOOLS_BY_LEVEL)}")

    tools = GatewayTools(jira=jira or JiraClient(), conn=conn or get_connection(), level=level)
    server: MCPServer = MCPServer(name="agentic-pm")
    registry = {
        "search_issues": tools.search_issues,
        "get_issue": tools.get_issue,
        "start_run": tools.start_run,
        "propose_estimate_change": tools.propose_estimate_change,
        "propose_due_date_change": tools.propose_due_date_change,
        "finish_run": tools.finish_run,
        "commit_changes": tools.commit_changes,
    }
    for name in TOOLS_BY_LEVEL[level]:
        server.add_tool(registry[name], name=name)
    return server


def main() -> None:
    level = os.environ.get("AGENTIC_PM_LEVEL", "L3")
    server = build_server(level)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
