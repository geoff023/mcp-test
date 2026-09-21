"""Turns raw audit_log rows into something a reviewer can actually scan:
plain-language action labels, a friendly name for who did it, and
consecutive same-run rows collapsed into one group. Presentation only -
the underlying audit_log table stays exactly as written (append-only,
untouched by anything here); this only changes how app/templates/audit.html
renders it.
"""

from __future__ import annotations

import itertools
import re
import sqlite3

_ACTION_LABELS = {
    "start_run": "Started a run",
    "propose_estimate_change": "Proposed an estimate",
    "propose_due_date_change": "Proposed a due-date change",
    "finish_run": "Finished staging",
    "commit_changes": "Applied to Jira",
    "approve": "Approved",
    "reject": "Sent back",
    "issue_token": "Issued a token",
    "chat_turn": "Answered a question",
    "recover_stuck_run": "Manually recovered",
}

_SUCCESS_OUTCOMES = {"ok", "staged", "applied", "issued", "answered"}
_FAILURE_OUTCOMES = {"refused", "rejected", "failed"}


def friendly_action(action: str) -> str:
    return _ACTION_LABELS.get(action, action.replace("_", " ").capitalize())


def friendly_actor(actor: str) -> str:
    """Deliberately doesn't collapse "agent:planning" into something that
    reads as AI - it is a human typing into Claude Code by hand (see
    gateway/server.py's DEFAULT_AGENT docstring); only the orchestrator
    identities (agent:orchestrator:...) are actually an LLM driving the
    gateway on its own."""
    if actor == "human:reviewer":
        return "You"
    if actor == "agent:planning":
        return "Human (via Claude Code)"
    if actor == "gateway":
        return "System"
    if actor == "agent:orchestrator:token-watcher":
        return "Token watcher (automated)"
    if actor == "agent:orchestrator:chat":
        return "Chat agent"
    if actor.startswith("agent:orchestrator:"):
        return f"AI agent ({actor.split(':', 2)[2]})"
    return actor


_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def friendly_detail(action: str, detail: str | None) -> str | None:
    """A short plain-language line for an audit row, or None when the raw
    detail is only plumbing (tool-call dumps, task_type=... scope=<JQL>,
    "call commit_changes"). Presentation only - the stored detail is never
    touched. Unknown formats fall through as-is only for the two actions
    whose detail is human text (a reviewer's comment, a refusal reason)."""
    if not detail:
        return None
    if action in ("chat_turn", "start_run", "issue_token"):
        return None
    if action == "propose_estimate_change":
        m = re.match(r"(\S+) -> ([\d.]+) pts$", detail)
        return f"{m[1]} set to {m[2]} points" if m else None
    if action == "propose_due_date_change":
        m = re.match(r"refused due-date change on (\S+):", detail)
        if m:
            return f"{m[1]} is a milestone, so its due date can't be changed"
        return detail.replace(" -> ", " to ")
    if action == "finish_run":
        m = re.match(r"(\d+) staged changes?$", detail)
        return f"{m[1]} {'change' if m[1] == '1' else 'changes'} staged" if m else None
    if action in ("approve", "commit_changes") and "issue(s)" in detail:
        keys = _ISSUE_KEY.findall(detail)
        return f"Applied to {', '.join(keys)}" if keys else None
    if action == "reject":
        return f"Reason: {detail}"
    return detail


def outcome_icon(outcome: str) -> str:
    if outcome in _SUCCESS_OUTCOMES:
        return "✓"
    if outcome in _FAILURE_OUTCOMES:
        return "✕"
    return "•"


def group_audit_rows(rows: list[sqlite3.Row]) -> list[dict]:
    """Groups consecutive rows sharing a run_id - rows must already be
    ordered by id DESC (newest first), which is how show_audit queries
    them, so each group's first row is that run's most recent action.
    Rows with no run_id (chat turns; runs.run_id is nullable - see
    db/migrations/0001_init.sql) are never grouped, each stands alone.
    Only actually collapses a group in the template when it has more than
    one row - a single-action run renders as a plain row, not a
    one-item disclosure that adds a click for nothing.
    """
    groups: list[dict] = []
    for run_id, chunk in itertools.groupby(rows, key=lambda r: r["run_id"]):
        chunk = list(chunk)
        if run_id is None:
            groups.extend({"run_id": None, "rows": [row]} for row in chunk)
        else:
            groups.append({"run_id": run_id, "rows": chunk})
    return groups


# Filter tabs on the Activity page: (key, label). "all" is always first.
ACTIVITY_FILTERS = [
    ("all", "All"),
    ("changed", "Changed Jira"),
    ("needs", "Needs you"),
    ("rejected", "Rejected"),
    ("advice", "Advice only"),
]


def group_kinds(group: dict) -> set[str]:
    """Which filter tabs a group belongs to (a group can be in several).
    changed: something was applied to Jira. needs: the run is still waiting
    on a human (its newest step is awaiting_review). rejected: sent back.
    advice: a chat answer - nothing was written."""
    rows = group["rows"]
    kinds = set()
    if any(r["outcome"] == "applied" for r in rows):
        kinds.add("changed")
    if rows[0]["outcome"] == "awaiting_review":
        kinds.add("needs")
    if any(r["action"] == "reject" for r in rows):
        kinds.add("rejected")
    if group["run_id"] is None and rows[0]["action"] == "chat_turn":
        kinds.add("advice")
    return kinds


def filter_groups(groups: list[dict], key: str) -> list[dict]:
    if key == "all" or key not in dict(ACTIVITY_FILTERS):
        return groups
    return [g for g in groups if key in group_kinds(g)]


def filter_counts(groups: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key, _ in ACTIVITY_FILTERS}
    counts["all"] = len(groups)
    for g in groups:
        for kind in group_kinds(g):
            counts[kind] += 1
    return counts
