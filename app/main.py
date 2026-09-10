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

from app.levels import level_name, level_tagline
from db.audit import log_audit
from db.migrate import get_connection
from gateway.apply import apply_staged_changes
from gateway.jira import JiraClient
from orchestrator.agent import OrchestratorError, run_reestimate_task

APP_DIR = Path(__file__).resolve().parent

# Slice 1 is explicitly out of scope for login (docs/slice-1.md); there is
# exactly one implicit reviewer.
HUMAN_ACTOR = "human:reviewer"
TOKEN_TTL_MINUTES = 15

app = FastAPI(title="agentic-pm control plane")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["level_name"] = level_name
templates.env.globals["level_tagline"] = level_tagline

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
        awaiting_review_count = _conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'awaiting_review'"
        ).fetchone()[0]
    return templates.TemplateResponse(
        request, "index.html", {"runs": runs, "awaiting_review_count": awaiting_review_count}
    )


@app.get("/agent-console")
def agent_console(request: Request):
    return templates.TemplateResponse(request, "agent_console.html", {})


def _build_target_jql(scope: str, specific_issues: str) -> str:
    """Turns the agent console's plain "which issues?" choice into JQL, so
    a naive user is never asked to write query syntax themselves."""
    project_key = _jira.config.project_key
    if scope == "specific":
        keys = [k.strip().upper() for k in specific_issues.replace(",", " ").split() if k.strip()]
        if not keys:
            raise ValueError("Enter at least one issue key (e.g. MCP-4) for 'Specific issue(s)'.")
        return f"key in ({', '.join(keys)})"
    if scope == "all":
        return f"project = {project_key} AND issuetype = Story"
    if scope == "unestimated":
        # cf[<number>] addresses the custom field by id, not display name -
        # JIRA_STORY_POINTS_FIELD is "customfield_10016"; JQL wants "10016".
        field_number = _jira.config.story_points_field.rsplit("_", 1)[-1]
        return f"project = {project_key} AND issuetype = Story AND cf[{field_number}] is EMPTY"
    raise ValueError(f"Unknown scope {scope!r}")


@app.post("/agent-console/run")
async def agent_console_run(
    scope: str = Form(...),
    specific_issues: str = Form(default=""),
    instructions: str = Form(default=""),
    level: str = Form(...),
):
    """Runs the orchestrator synchronously and redirects into the existing
    review surface once it reaches awaiting_review. Blocks for the
    duration of the LLM's tool-use loop - no background job queue, see
    docs/slice-3.md section 2 (explicitly out of scope for this slice).
    """
    try:
        target_jql = _build_target_jql(scope, specific_issues)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        run_id = await run_reestimate_task(level=level, target_jql=target_jql, instructions=instructions)
    except OrchestratorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        changes = _conn.execute(
            "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
        ).fetchall()

        # L4 has no direct approve - a token has to be issued (only once every
        # staged change is acknowledged) and then consumed by the agent's own
        # commit_changes call. Work out what state that handoff is in so the
        # template can show either the acknowledge/issue-token controls or the
        # issued token itself.
        active_token = None
        all_acknowledged = False
        if run["level"] == "L4":
            active_token = _conn.execute(
                "SELECT token, expires_at FROM approval_tokens "
                "WHERE run_id = ? AND consumed_at IS NULL ORDER BY issued_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if active_token is not None:
                expires_at = datetime.fromisoformat(active_token["expires_at"])
                if expires_at < datetime.now(timezone.utc):
                    active_token = None  # expired - treat as if none was issued
            pending = [c for c in changes if c["applied_at"] is None]
            all_acknowledged = bool(pending) and all(c["acknowledged"] for c in pending)

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

    return templates.TemplateResponse(
        request,
        "run.html",
        {"run": run, "rows": rows, "active_token": active_token, "all_acknowledged": all_acknowledged},
    )


@app.post("/runs/{run_id}/approve")
async def approve_run(request: Request, run_id: str):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")
        if run["level"] == "L4":
            raise HTTPException(
                status_code=400,
                detail="L4 runs are approved via /issue-token and the agent's commit_changes call, not /approve",
            )

        if run["level"] == "L2":
            # The review page posted one value_<change_id> field per row -
            # a human may have changed some of them. Only set edited_value
            # where it actually differs, so was_edited stays honest.
            form = await request.form()
            pending = _conn.execute(
                "SELECT id, new_value FROM staged_changes WHERE run_id = ? AND applied_at IS NULL",
                (run_id,),
            ).fetchall()
            for change in pending:
                submitted = form.get(f"value_{change['id']}")
                if submitted is not None and submitted != change["new_value"]:
                    _conn.execute(
                        "UPDATE staged_changes SET edited_value = ? WHERE id = ?",
                        (submitted, change["id"]),
                    )
            _conn.commit()

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

        applied_keys = [a["issue_key"] for a in applied]
        edited = [a["issue_key"] for a in applied if a["was_edited"]]
        detail = f"{len(applied)} issue(s) applied: {applied_keys}"
        if edited:
            detail += f" ({len(edited)} edited from the agent's proposal: {edited})"
        log_audit(
            _conn,
            run_id=run_id,
            actor=HUMAN_ACTOR,
            level=run["level"],
            action="approve",
            outcome="applied",
            detail=detail,
        )
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/changes/{change_id}/acknowledge")
def acknowledge_change(run_id: str, change_id: str):
    """L4 only: toggle one staged change's acknowledged flag. issue_token
    below refuses until every staged change on the run has this set."""
    with _lock:
        run = _conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")

        change = _conn.execute(
            "SELECT acknowledged FROM staged_changes WHERE id = ? AND run_id = ?", (change_id, run_id)
        ).fetchone()
        if change is None:
            raise HTTPException(status_code=404, detail="staged change not found")

        _conn.execute(
            "UPDATE staged_changes SET acknowledged = ? WHERE id = ?",
            (0 if change["acknowledged"] else 1, change_id),
        )
        _conn.commit()
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/issue-token")
def issue_token(run_id: str):
    """L4 only: issue an approval_tokens row once every staged change is
    acknowledged. Applies nothing and does not touch runs.status - the
    token only takes effect when the agent calls commit_changes with it.
    """
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["level"] != "L4":
            raise HTTPException(status_code=400, detail="issue-token is only for L4 runs")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")

        total, unacknowledged = _conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN acknowledged = 0 THEN 1 ELSE 0 END) "
            "FROM staged_changes WHERE run_id = ? AND applied_at IS NULL",
            (run_id,),
        ).fetchone()
        if not total:
            raise HTTPException(status_code=400, detail="no staged changes to acknowledge")
        if unacknowledged:
            raise HTTPException(
                status_code=400,
                detail=f"{unacknowledged} of {total} staged change(s) not yet acknowledged",
            )

        token = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()
        _conn.execute(
            "INSERT INTO approval_tokens (token, run_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, run_id, _now(), expires_at),
        )
        _conn.commit()

        log_audit(
            _conn,
            run_id=run_id,
            actor=HUMAN_ACTOR,
            level=run["level"],
            action="issue_token",
            outcome="issued",
            detail=f"token expires {expires_at}; hand it to the agent to call commit_changes",
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
