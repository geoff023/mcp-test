"""Plain-language rendering of run fields for the Runs and run-detail pages:
a readable scope instead of raw JQL, dates instead of ISO timestamps, and
sentence-case labels for stored codes like "awaiting_review".

Presentation only - runs.scope / runs.created_at are stored exactly as the
gateway wrote them; the raw values stay available as hover text.
"""

from __future__ import annotations

import re
from datetime import datetime

_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def humanize(code: str | None) -> str:
    """"awaiting_review" -> "Awaiting review"; "story_points" -> "Story points"."""
    return (code or "").replace("_", " ").capitalize()


def friendly_scope(scope: str | None) -> str:
    """What a run was pointed at, in the same words the Agent console uses.

    Scope is stored as JQL (or, for runs started by hand in Claude Code, as
    whatever that session typed - "MCP-4 (L2 edit test)", "key = MCP-2"), so
    this recognises the three shapes the console produces plus those looser
    forms, and falls back to the raw text rather than guessing.
    """
    text = (scope or "").strip()
    if re.search(r"\bis\s+empty\b", text, re.IGNORECASE):
        return "Stories with no estimate yet"
    if not re.search(r"\bproject\s*=", text, re.IGNORECASE):
        keys = _ISSUE_KEY.findall(text)
        if keys:
            return f"Issue {keys[0]}" if len(keys) == 1 else f"Issues {', '.join(keys)}"
    elif re.search(r"\bissuetype\s*=\s*story\b", text, re.IGNORECASE):
        return "All stories in this project"
    return text


def _local(ts: str) -> datetime:
    """Timestamps are stored in UTC; show them in the viewer's (server's) local
    time. Everyone runs this app on their own machine, so that is their time."""
    return datetime.fromisoformat(ts).astimezone()


def short_date(ts: str | None) -> str:
    if not ts:
        return ""
    d = _local(ts)
    return f"{d.day} {d:%b %Y}"


def full_datetime(ts: str | None, *, seconds: bool = True) -> str:
    if not ts:
        return ""
    d = _local(ts)
    return f"{d.day} {d:%b %Y}, {d:%H:%M:%S}" if seconds else f"{d.day} {d:%b %Y}, {d:%H:%M}"


def clock_time(ts: str | None) -> str:
    return _local(ts).strftime("%H:%M:%S") if ts else ""
