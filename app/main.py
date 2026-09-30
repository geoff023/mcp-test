"""The control-plane web app: four server-rendered routes, no JS framework,
no build step (see CLAUDE.md section 4).

Approving or rejecting a run happens directly in this process, not through
the MCP gateway - a human clicking Approve in this app is what actually
writes to Jira, at every level including L4. L4 additionally requires the
batch to be authorised first (/runs/{run_id}/authorize acknowledges every
staged change at once); Approve refuses until that's done. Approve still
issues and consumes an approval_tokens row for every level, for audit
trail parity - the agent's own commit_changes tool (gateway/server.py)
remains available for anyone driving a run by hand instead of through
this app, but the product UI never depends on it.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import jobs
from app.analytics import STATUS_CATEGORY_LABELS, STATUS_CATEGORY_ORDER, build_dashboard_data
from app.run_display import ago, clock_time, friendly_scope, full_datetime, humanize, short_date
from app.audit_display import (
    ACTIVITY_FILTERS,
    filter_counts,
    filter_groups,
    friendly_action,
    friendly_actor,
    friendly_detail,
    group_audit_rows,
    outcome_icon,
)
from app.charts import area_chart, bar_chart, donut_chart
from app.chat_markdown import render_chat_markdown
from app.levels import (
    LEVEL_DISPLAY,
    MODES,
    READ_TOOLS,
    TASK_TYPES,
    level_name,
    level_tagline,
    mode_code,
    mode_name,
    mode_tagline,
    tool_label,
)
from db.audit import log_audit
from db.migrate import get_connection
from gateway.apply import apply_staged_changes
from gateway.jira import JiraClient
from gateway.server import TOOLS_BY_LEVEL
from orchestrator.agent import OrchestratorError, run_reestimate_task
from orchestrator.chat import ChatError, run_chat_turn

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
templates.env.globals["mode_name"] = mode_name
templates.env.globals["mode_tagline"] = mode_tagline
templates.env.globals["MODES"] = MODES
templates.env.globals["render_chat_markdown"] = render_chat_markdown
templates.env.globals["friendly_action"] = friendly_action
templates.env.globals["friendly_actor"] = friendly_actor
templates.env.globals["friendly_detail"] = friendly_detail
for _fn in (ago, clock_time, friendly_scope, full_datetime, humanize, short_date, tool_label):
    templates.env.globals[_fn.__name__] = _fn
templates.env.globals["outcome_icon"] = outcome_icon

_STYLE_CSS_PATH = APP_DIR / "static" / "style.css"


def _static_version() -> int:
    """Cache-buster for /static/style.css. StaticFiles sends no explicit
    Cache-Control header, so browsers fall back to heuristic caching and
    can keep serving a stale copy indefinitely even across a normal
    reload - confirmed directly this session (a plain navigate, and even
    a force-navigate, both kept serving an old cached stylesheet; only an
    explicit cache:'no-store' fetch got the current one). Appending this
    file's mtime to the stylesheet URL in base.html means the URL itself
    changes the moment the file does, so a normal page load always gets
    the current CSS - no server restart or manual cache-bust needed,
    here or for anyone actually using the app.
    """
    return int(_STYLE_CSS_PATH.stat().st_mtime)


templates.env.globals["static_version"] = _static_version


def _script_version() -> int:
    """Same cache-busting idea as _static_version, for static/*.js."""
    return int(max(p.stat().st_mtime for p in (APP_DIR / "static").glob("*.js")))


templates.env.globals["script_version"] = _script_version

_conn = get_connection()
_conn.row_factory = sqlite3.Row
_jira = JiraClient()
_lock = threading.RLock()  # re-entrant: shell() reads the DB during template rendering


def _shell() -> dict:
    """What the sidebar and top bar show on every page: the Jira project, the
    Approvals badge (runs waiting on a human) and a link out to Jira. The
    'Jira site' card reports that credentials are configured, not a live check -
    a per-page-view round trip to Jira would make every page slow."""
    config = getattr(_jira, "config", None)
    base = (getattr(config, "base_url", "") or "").rstrip("/")
    key = getattr(config, "project_key", "") or ""
    with _lock:
        awaiting = _conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'awaiting_review'").fetchone()[0]
    return {
        "awaiting": awaiting,
        "project_key": key,
        "jira_host": urlparse(base).netloc if base else "",
        "jira_url": f"{base}/browse/{key}" if base and key else "",
    }


templates.env.globals["shell"] = _shell


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
def home():
    """The prototype's first nav item, and so the landing page, is the Dashboard."""
    return RedirectResponse(url="/dashboard", status_code=307)


@app.get("/approvals")
def list_runs(request: Request):
    """The decision inbox: runs waiting on a human first (oldest first, so
    nothing sits forgotten), then every earlier run."""
    with _lock:
        runs = _conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        change_counts = {
            row["run_id"]: row["n"]
            for row in _conn.execute("SELECT run_id, COUNT(*) AS n FROM staged_changes GROUP BY run_id")
        }
    waiting = sorted((r for r in runs if r["status"] == "awaiting_review"), key=lambda r: r["created_at"])
    past = [r for r in runs if r["status"] != "awaiting_review"]
    return templates.TemplateResponse(
        request,
        "index.html",
        {"waiting": waiting, "past": past, "change_counts": change_counts, "awaiting_review_count": len(waiting)},
    )


@app.get("/agent-console")
def agent_console(request: Request, scope: str = "unestimated"):
    """Super-Pilot's (M2) front door. `scope` pre-selects the "which issues?"
    radio - e.g. the dashboard's "N stories not yet estimated" suggestion
    links straight to ?scope=unestimated so a human doesn't have to make
    that choice by hand after already being told what needs doing.

    Every run started here dispatches at L2 - Co-Pilot (L1) lives entirely
    at /chat, and Auto-Pilot (L4) has its own page at /autopilot.
    """
    return templates.TemplateResponse(
        request,
        "agent_console.html",
        {
            "default_scope": scope,
            "task_types": TASK_TYPES,
            "level_facts": {code: {"creates": d["creates"], "writes": d["writes"]} for code, d in LEVEL_DISPLAY.items()},
        },
    )


_AUTOMATION_RULES = {
    "reestimate": "Automatically update all tasks with no story points",
    "weekly_status": "Automatically post a weekly status report every Friday",
    "standup_digest": "Automatically summarise standup activity each morning",
    "risk_scan": "Automatically flag new risk signals across the backlog",
    "sprint_planning": "Automatically propose next sprint's scope from velocity",
    "retrospective": "Automatically draft a retrospective summary at sprint close",
}
_AUTOMATION_CREATED = ["3 days ago", "6 days ago", "1 week ago", "2 weeks ago", "3 weeks ago", "1 month ago"]


def _seeded_automations() -> list[dict]:
    """Auto-Pilot's default automation list. Only the reestimate one is
    real - its Run now button POSTs to the same tested /agent-console/start
    path Super-Pilot uses at L2 (see autopilot.html). Everything else here
    is placeholder data illustrating what a fuller automation list would
    look like once those task types are built; their Run now buttons stay
    disabled rather than pretend to trigger a task the orchestrator can't
    actually run yet. The forecast automation isn't a task type at all -
    it's the dashboard's own pace-based forecast, shown here as what
    watching that forecast automatically would look like.
    """
    automations = [
        {
            "slug": t["slug"],
            "name": t["label"],
            "rule": _AUTOMATION_RULES[t["slug"]],
            "task_label": t["label"],
            "scope_label": "Stories with no estimate yet" if t["slug"] == "reestimate" else "Whole project",
            "scope_value": "unestimated" if t["slug"] == "reestimate" else "all",
            "available": t["available"],
            "created": _AUTOMATION_CREATED[i % len(_AUTOMATION_CREATED)],
            "active": t["available"],
        }
        for i, t in enumerate(TASK_TYPES)
    ]
    automations.append(
        {
            "slug": "forecast_watch",
            "name": "Forecast pace risk",
            "rule": "Automatically flag when pace falls behind the forecast",
            "task_label": "Forecast watch",
            "scope_label": "Whole project",
            "scope_value": "all",
            "available": False,
            "created": "4 days ago",
            "active": False,
        }
    )
    return automations


@app.get("/autopilot")
def autopilot(request: Request):
    """Auto-Pilot's (M3) front door: its own page, not a mode toggle on
    Super-Pilot's wizard - see the "configure vs active automations"
    split in autopilot.html. Only a real (available) automation's "Run
    now" action does anything (POSTs to /agent-console/start at level=L4,
    the same tested path Super-Pilot uses at L2); the Active-automations
    list itself - toggle, delete, every seeded row - is client-side only,
    nothing persisted, no automations table this milestone. Say so on the
    page rather than imply otherwise.
    """
    return templates.TemplateResponse(
        request,
        "autopilot.html",
        {
            "default_scope": "unestimated",
            "task_types": TASK_TYPES,
            "level_facts": LEVEL_DISPLAY["L4"],
            "seeded_automations": _seeded_automations(),
        },
    )


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

    L1 can't drive this task type at all - it has no start_run tool
    (TOOLS_BY_LEVEL in gateway/server.py) - so it never reaches the
    orchestrator here. The wizard itself no longer offers L1 (Co-Pilot
    lives entirely at /chat - see app/levels.py's module docstring), so
    this only guards anyone who posts here directly with level=L1.
    """
    if level == "L1":
        return RedirectResponse(url="/chat", status_code=303)
    try:
        target_jql = _build_target_jql(scope, specific_issues)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        run_id = await run_reestimate_task(level=level, target_jql=target_jql, instructions=instructions)
    except OrchestratorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.post("/agent-console/start")
async def agent_console_start(
    scope: str = Form(...),
    specific_issues: str = Form(default=""),
    instructions: str = Form(default=""),
    level: str = Form(...),
):
    """Same task as /agent-console/run, but the orchestrator keeps going in the
    background and this returns at once with a job id; the console page polls
    GET /jobs/{id} to show each real step and goes to the run's review page
    when it finishes. /agent-console/run stays as the no-JavaScript path."""
    if level == "L1":
        raise HTTPException(status_code=400, detail="Consultant mode has no run to start - use the chat.")
    try:
        target_jql = _build_target_jql(scope, specific_issues)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def work(job: jobs.Job) -> str:
        run_id = await run_reestimate_task(
            level=level, target_jql=target_jql, instructions=instructions, on_step=job.on_step
        )
        return f"/runs/{run_id}"

    return {"job_id": jobs.start("run", work).id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job - the server may have restarted. Try again.")
    return job.to_json()


def _status_note(run: sqlite3.Row, active_token: sqlite3.Row | None, all_acknowledged: bool) -> str:
    """A short, plain-language line about what this run's status actually
    means and what (if anything) happens next. The review flow - what's
    reached Jira and what hasn't - is the part a first-time user gets lost
    in, so say it explicitly rather than leaving the status pill alone to
    carry that."""
    status = run["status"]
    if status == "running":
        return "The agent is still working through this task - refresh in a moment."
    if status == "rejected":
        return "Sent back - nothing from this run reached Jira."
    if status == "applied":
        return "Applied to Jira. This record is now closed - see the audit log for exactly what changed."
    if status == "awaiting_review":
        if run["level"] != "L4":
            return "Nothing has reached Jira yet - review what's below, then Approve or send it back."
        if active_token is not None:
            # Only reachable via the manual /issue-token API path - the UI's own
            # authorise-then-approve flow never issues a token itself.
            return "A token has been issued - Jira updates once the agent spends it, not before."
        if all_acknowledged:
            return "Batch authorised - click Approve to write it to Jira."
        return "Nothing has reached Jira yet - authorise the batch below, then Approve to write it to Jira."
    return ""


@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        changes = _conn.execute(
            "SELECT * FROM staged_changes WHERE run_id = ? ORDER BY issue_key", (run_id,)
        ).fetchall()

        # L4 has no direct approve - a token has to be issued (via /authorize,
        # which acknowledges the whole batch and issues it in one step) and
        # then consumed by the agent's own commit_changes call. Work out
        # whether that token is out yet so the template can show either the
        # Authorize button or the issued token itself.
        active_token = None
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

    all_acknowledged = bool(changes) and all(c["acknowledged"] for c in changes)

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
        {
            "run": run,
            "rows": rows,
            "active_token": active_token,
            "all_acknowledged": all_acknowledged,
            "status_note": _status_note(run, active_token, all_acknowledged),
        },
    )


@app.post("/runs/{run_id}/approve")
async def approve_run(request: Request, run_id: str):
    """Writes every staged change straight to Jira, for every level -
    including L4, whose batch must already be authorised (see
    /runs/{run_id}/authorize) before this is allowed. The agent's own
    commit_changes tool (gateway/server.py) stays available for anyone
    driving a run by hand instead of through this app, but the product UI
    never waits on it - a human clicking Approve is what actually writes."""
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")
        if run["level"] == "L4":
            total, unacknowledged = _conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN acknowledged = 0 THEN 1 ELSE 0 END) "
                "FROM staged_changes WHERE run_id = ? AND applied_at IS NULL",
                (run_id,),
            ).fetchone()
            if not total:
                raise HTTPException(status_code=400, detail="no staged changes to approve")
            if unacknowledged:
                raise HTTPException(
                    status_code=400,
                    detail=f"{unacknowledged} of {total} staged change(s) not yet authorised - "
                    "authorise the batch first",
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


@app.post("/runs/{run_id}/authorize")
def authorize_run(run_id: str):
    """Auto-Pilot's first click: marks every staged change on the run
    acknowledged at once, instead of one at a time. Applies nothing and
    issues no token - it only unlocks the Approve button on this run's page
    (see approve_run), which is the second click and the one that actually
    writes to Jira. /changes/{id}/acknowledge still exists underneath for
    anyone driving a run by hand (e.g. via Claude Code, see CLAUDE.md).
    """
    with _lock:
        run = _conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run["level"] != "L4":
            raise HTTPException(status_code=400, detail="authorize is only for L4 runs")
        if run["status"] != "awaiting_review":
            raise HTTPException(status_code=400, detail=f"run is {run['status']}, not awaiting_review")

        (total,) = _conn.execute(
            "SELECT COUNT(*) FROM staged_changes WHERE run_id = ? AND applied_at IS NULL", (run_id,)
        ).fetchone()
        if not total:
            raise HTTPException(status_code=400, detail="no staged changes to authorize")

        _conn.execute(
            "UPDATE staged_changes SET acknowledged = 1 WHERE run_id = ? AND applied_at IS NULL", (run_id,)
        )
        _conn.commit()

        log_audit(
            _conn,
            run_id=run_id,
            actor=HUMAN_ACTOR,
            level=run["level"],
            action="authorize",
            outcome="acknowledged",
            detail=f"{total} change(s) authorised",
        )
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/reject")
def reject_run(run_id: str, comment: str = Form(default=""), redirect_to: str = Form(default="")):
    """Rejecting takes a run out of the Approvals queue (its status stops
    being 'awaiting_review') and logs the decision to Activity - nothing
    reached Jira, so there is nothing to undo there. `redirect_to` lets the
    Approvals queue's own quick-reject button send the human back to the
    list it just changed, instead of always landing on the run page - same
    allowlisted-target pattern as POST /chat's redirect_to."""
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
    target = "/approvals" if redirect_to == "/approvals" else f"/runs/{run_id}"
    return RedirectResponse(url=target, status_code=303)


@app.get("/audit")
def show_audit(request: Request, show: str = "all"):
    with _lock:
        rows = _conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchall()
    groups = group_audit_rows(rows)
    if show not in dict(ACTIVITY_FILTERS):
        show = "all"
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "groups": filter_groups(groups, show),
            "filters": ACTIVITY_FILTERS,
            "counts": filter_counts(groups),
            "show": show,
        },
    )


@app.get("/settings")
def show_settings(request: Request):
    """Read-only: what each product mode may do, straight from the tool table
    the gateway enforces (TOOLS_BY_LEVEL) - not a copy that could drift.

    One row per mode (M1 Co-Pilot, M2 Super-Pilot, M3 Auto-Pilot), not per
    L-code - L3 is tool-identical to L2 (same TOOLS_BY_LEVEL entries, only
    the UI-enforced locked-vs-editable review differs), so a separate L3
    row would just repeat L2's row under the same "Super-Pilot" name and
    look like a bug. L2 stands in for both here.
    """
    modes = [
        {
            "code": code,
            "mode": mode_name(code),
            "tagline": mode_tagline(code),
            "creates": LEVEL_DISPLAY[code]["creates"],
            "writes": LEVEL_DISPLAY[code]["writes"],
            "tools": [{"label": tool_label(t), "write": t not in READ_TOOLS} for t in TOOLS_BY_LEVEL[code]],
        }
        for code in ("L1", "L2", "L4")
    ]
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"modes": modes, "task_types": TASK_TYPES, "token_ttl_minutes": TOKEN_TTL_MINUTES},
    )


_STATUS_CATEGORY_COLORS = ("var(--viz-todo)", "var(--viz-doing)", "var(--viz-done)")


@app.get("/dashboard")
def show_dashboard(request: Request):
    """Read-only analytics over the whole project - see app/analytics.py
    for how the numbers and forecast are derived, app/charts.py for how
    the SVGs are drawn. No caching: this is a small demo project, and a
    stale dashboard is worse than one extra Jira round-trip per view.
    """
    jql = f"project = {_jira.config.project_key} ORDER BY created ASC"
    jira_error = None
    try:
        rows = _jira.search_issues_for_analytics(jql)
    except Exception as exc:  # noqa: BLE001 - the landing page must still load without Jira
        print(f"dashboard: Jira unreachable: {exc!r}", file=sys.stderr)
        rows, jira_error = [], "Couldn't reach Jira, so project numbers are empty. Runs and approvals below still come from CogniPM."
    data = build_dashboard_data(rows)

    with _lock:
        run_counts = {r["level"]: r["n"] for r in _conn.execute("SELECT level, COUNT(*) AS n FROM runs GROUP BY level")}
        pending_level_codes = {
            r["level"] for r in _conn.execute("SELECT DISTINCT level FROM runs WHERE status = 'awaiting_review'")
        }
    run_total = sum(run_counts.values())
    pending_modes = len({mode_code(code) for code in pending_level_codes})
    # Group by mode, not raw level - M2 sums L2+L3 into one bucket, so the
    # dashboard's own breakdown matches the 3-mode nav rather than showing a
    # 4th bucket nothing links to any more. Each mode keeps one representative
    # L-code (its own `level` in MODES) purely for the existing per-level
    # colour classes in the template - no new CSS needed for this redesign.
    mode_counts: dict[str, int] = {}
    for level_code, n in run_counts.items():
        mode_counts[mode_code(level_code)] = mode_counts.get(mode_code(level_code), 0) + n
    run_split = [
        {
            "level": mode["level"],
            "name": mode["name"],
            "count": mode_counts[mode["code"]],
            "pct": max(round(mode_counts[mode["code"]] / run_total * 100), 8),
        }
        for mode in MODES
        if mode_counts.get(mode["code"])
    ]
    points_pct = round(data.done_points / data.total_points * 100) if data.total_points else 0

    status_bars = [
        {"label": STATUS_CATEGORY_LABELS[key], "value": data.status_counts[key], "color": color}
        for key, color in zip(STATUS_CATEGORY_ORDER, _STATUS_CATEGORY_COLORS)
    ]
    estimate_segments = [
        {"label": "Estimated", "value": data.estimated_count, "color": "var(--brand)"},
        {"label": "Unestimated", "value": data.unestimated_count, "color": "var(--border)"},
    ]
    total_stories = data.estimated_count + data.unestimated_count
    estimated_pct = round((data.estimated_count / total_stories) * 100) if total_stories else 0
    burndown_points = [{"label": label, "value": value} for label, value in data.burndown]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "data": data,
            "status_bars": status_bars,
            "estimate_segments": estimate_segments,
            "estimated_pct": estimated_pct,
            "status_chart": bar_chart(status_bars),
            "estimate_chart": donut_chart(estimate_segments, center_label="estimated", center_value=f"{estimated_pct}%"),
            "burndown_chart": area_chart(burndown_points),
            "jira_error": jira_error,
            "run_total": run_total,
            "run_split": run_split,
            "pending_modes": pending_modes,
            "points_pct": points_pct,
        },
    )


# ---------- L1 (Consultant) chat - read-only, see orchestrator/chat.py ----------

CHAT_AGENT_IDENTITY = "agent:orchestrator:chat"


def _get_or_create_chat_session() -> str:
    """One ongoing conversation, the same "exactly one implicit reviewer"
    simplification slice 1 made for HUMAN_ACTOR - no login, no session
    picker, just the single thread this deployment has."""
    row = _conn.execute("SELECT id FROM chat_sessions ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is not None:
        return row["id"]
    session_id = str(uuid.uuid4())
    _conn.execute("INSERT INTO chat_sessions (id, created_at) VALUES (?, ?)", (session_id, _now()))
    _conn.commit()
    return session_id


@app.get("/chat")
def show_chat(request: Request):
    with _lock:
        session_id = _get_or_create_chat_session()
        messages = _conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return templates.TemplateResponse(request, "chat.html", {"messages": messages})


_CHAT_REDIRECT_TARGETS = {"/chat"}


def _load_chat_history() -> tuple[str, list[dict]]:
    with _lock:
        session_id = _get_or_create_chat_session()
        history = _conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return session_id, [{"role": row["role"], "content": row["content"]} for row in history]


def _record_chat_turn(session_id: str, message: str, answer: str, tool_calls: list[str], outcome: str) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
            (session_id, message, _now()),
        )
        _conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, 'agent', ?, ?)",
            (session_id, answer, _now()),
        )
        _conn.commit()
        log_audit(
            _conn,
            run_id=None,
            actor=CHAT_AGENT_IDENTITY,
            level="L1",
            action="chat_turn",
            outcome=outcome,
            detail=f"tool_calls={tool_calls}" if tool_calls else "no tool calls",
        )


@app.post("/chat")
async def post_chat(message: str = Form(...), redirect_to: str = Form(default="/chat")):
    """`redirect_to` lets the embedded chat step in agent_console.html send
    the human back to the console (reopening on the chat step - see
    agent_console()'s docstring) instead of always landing on the
    standalone /chat page. Restricted to a fixed allowlist rather than
    trusted as-is - this is form input, not a hardcoded template value,
    even though only our own templates currently set it. This is the
    no-JavaScript path; with JavaScript the page uses /chat/start."""
    if redirect_to not in _CHAT_REDIRECT_TARGETS:
        redirect_to = "/chat"
    session_id, history = _load_chat_history()

    try:
        answer, tool_calls = await run_chat_turn(history=history, message=message)
        outcome = "answered"
    except ChatError as exc:
        answer = f"Sorry, I couldn't answer that: {exc}"
        tool_calls = []
        outcome = "failed"

    _record_chat_turn(session_id, message, answer, tool_calls, outcome)
    return RedirectResponse(url=redirect_to, status_code=303)


@app.post("/chat/start")
async def post_chat_start(message: str = Form(...)):
    """Background variant of POST /chat: the turn runs while the page shows
    the assistant's real steps (GET /jobs/{id}); the page then reloads to
    show the saved thread, exactly as after the blocking route."""
    session_id, history = _load_chat_history()

    async def work(job: jobs.Job) -> None:
        try:
            answer, tool_calls = await run_chat_turn(history=history, message=message, on_step=job.on_step)
            outcome = "answered"
        except ChatError as exc:
            answer = f"Sorry, I couldn't answer that: {exc}"
            tool_calls = []
            outcome = "failed"
        _record_chat_turn(session_id, message, answer, tool_calls, outcome)

    return {"job_id": jobs.start("chat", work).id}
