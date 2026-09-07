"""The control-plane web app: four server-rendered routes, no JS framework,
no build step (see CLAUDE.md section 4).

Approving or rejecting a run happens directly in this process, not through
the MCP gateway - at L3 the agent has no commit_changes tool at all, so a
human acting through this app is the only way a staged change ever
reaches Jira. Approve still issues and consumes an approval_tokens row,
for audit parity with the L4 path.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db.audit import log_audit
from db.migrate import get_connection
from gateway.apply import apply_staged_changes
from gateway.jira import JiraClient

APP_DIR = Path(__file__).resolve().parent

# Slice 1 is explicitly out of scope for login (docs/slice-1.md); there is
# exactly one implicit reviewer.
HUMAN_ACTOR = "human:reviewer"
TOKEN_TTL_MINUTES = 15

app = FastAPI(title="agentic-pm control plane")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_conn = get_connection()
_conn.row_factory = sqlite3.Row
_jira = JiraClient()
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_old_value(change: sqlite3.Row, live_issue) -> object:
    """Prefer the value staged at propose time. Falls back to a live Jira
    read for fields the propose tool deliberately doesn't fetch (story
    points - see gateway/server.py's propose_estimate_change), but only
    while the run is still pending: once applied, a live read would show
    the *new* value, not the old one, so we say plainly that it wasn't
    recorded rather than show something misleading.
    """
    if change["old_value"] is not None:
        return change["old_value"]
    if change["applied_at"] is not None or live_issue is None:
        return "(not recorded)"
    if change["field"] == "story_points":
        return live_issue.story_points if live_issue.story_points is not None else "(none)"
    if change["field"] == "due_date":
        return live_issue.due_date if live_issue.due_date is not None else "(none)"
    return "(not recorded)"


@app.get("/")
def list_runs(request: Request):
    with _lock:
        runs = _conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    return templates.TemplateResponse(request, "index.html", {"runs": runs})


@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        changes = _conn.execute(
            "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
        ).fetchall()

    rows = []
    for change in changes:
        summary = change["issue_key"]
        live_issue = None
        try:
            live_issue = _jira.get_issue(change["issue_key"])
            summary = live_issue.summary
        except Exception:
            pass  # Jira unreachable or issue gone - still show the staged row.
        rows.append(
            {
                "change": change,
                "summary": summary,
                "old_value": _display_old_value(change, live_issue),
            }
        )

    return templates.TemplateResponse(request, "run.html", {"run": run, "rows": rows})


@app.post("/runs/{run_id}/approve")
def approve_run(run_id: str):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")

        token = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()
        _conn.execute(
            "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, run_id, _now(), expires_at),
        )
        _conn.commit()

        applied = apply_staged_changes(_jira, _conn, run_id, actor=HUMAN_ACTOR, level=run["level"])

        _conn.execute("UPDATE approval_tokens SET consumed_at = ? WHERE token = ?", (_now(), token))
        _conn.execute("UPDATE runs SET status = 'applied' WHERE id = ?", (run_id,))
        _conn.commit()

        log_audit(
            _conn,
            run_id=run_id,
            actor=HUMAN_ACTOR,
            level=run["level"],
            action="approve",
            outcome="applied",
            detail=f"{len(applied)} issue(s) applied: {applied}",
        )
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/reject")
def reject_run(run_id: str, comment: str = Form(default="")):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")

        _conn.execute("UPDATE runs SET status = 'rejected', comment = ? WHERE id = ?", (comment, run_id))
        _conn.commit()

        log_audit(
            _conn,
            run_id=run_id,
            actor=HUMAN_ACTOR,
            level=run["level"],
            action="reject",
            outcome="rejected",
            detail=comment or None,
        )
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.get("/audit")
def show_audit(request: Request):
    with _lock:
        rows = _conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchall()
    return templates.TemplateResponse(request, "audit.html", {"rows": rows})
