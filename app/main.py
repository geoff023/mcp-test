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

from app.analytics import STATUS_CATEGORY_LABELS, STATUS_CATEGORY_ORDER, build_dashboard_data
from app.run_display import clock_time, friendly_scope, full_datetime, humanize, short_date
from app.audit_display import friendly_action, friendly_actor, friendly_detail, group_audit_rows, outcome_icon
from app.charts import area_chart, bar_chart, donut_chart
from app.chat_markdown import render_chat_markdown
from app.levels import level_name, level_tagline
from db.audit import log_audit
from db.migrate import get_connection
from gateway.apply import apply_staged_changes
from gateway.jira import JiraClient
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
templates.env.globals["render_chat_markdown"] = render_chat_markdown
templates.env.globals["friendly_action"] = friendly_action
templates.env.globals["friendly_actor"] = friendly_actor
templates.env.globals["friendly_detail"] = friendly_detail
for _fn in (clock_time, friendly_scope, full_datetime, humanize, short_date):
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
def agent_console(request: Request, scope: str = "unestimated", level: str = "L3"):
    """`scope` pre-selects the "which issues?" radio - e.g. the dashboard's
    "N stories not yet estimated" suggestion links straight to
    ?scope=unestimated so a human doesn't have to make that choice by hand
    after already being told what needs doing. `level` pre-selects the
    working-mode step and, for L1, which panel the wizard opens on -
    POST /chat's redirect_to sends a human back here with ?level=L1 after
    sending a message, so the console reopens on the chat step instead of
    resetting to step 1.

    Always loads the L1 chat thread (not just when level=L1) so the
    embedded chat step (see agent_console.html) has something to render
    the moment a human switches to it, without a second request.
    """
    with _lock:
        session_id = _get_or_create_chat_session()
        messages = _conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return templates.TemplateResponse(
        request, "agent_console.html", {"default_scope": scope, "default_level": level, "chat_messages": messages}
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
    orchestrator here; the wizard's own JS shows the embedded chat step
    instead of submitting this form when L1 is selected (see
    agent_console.html), this is the same guard server-side for anyone
    who posts here directly or has JS disabled.
    """
    if level == "L1":
        return RedirectResponse(url="/agent-console?level=L1", status_code=303)
    try:
        target_jql = _build_target_jql(scope, specific_issues)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        run_id = await run_reestimate_task(level=level, target_jql=target_jql, instructions=instructions)
    except OrchestratorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


def _status_note(run: sqlite3.Row, active_token: sqlite3.Row | None) -> str:
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
            return "A token has been issued - Jira updates once the agent spends it, not before."
        return (
            "Nothing has reached Jira yet - acknowledge each change below, "
            "then issue a token for the agent to spend."
        )
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
        {
            "run": run,
            "rows": rows,
            "active_token": active_token,
            "all_acknowledged": all_acknowledged,
            "status_note": _status_note(run, active_token),
        },
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
    return templates.TemplateResponse(request, "audit.html", {"groups": group_audit_rows(rows)})


_STATUS_CATEGORY_COLORS = ("var(--viz-todo)", "var(--viz-doing)", "var(--viz-done)")


@app.get("/dashboard")
def show_dashboard(request: Request):
    """Read-only analytics over the whole project - see app/analytics.py
    for how the numbers and forecast are derived, app/charts.py for how
    the SVGs are drawn. No caching: this is a small demo project, and a
    stale dashboard is worse than one extra Jira round-trip per view.
    """
    jql = f"project = {_jira.config.project_key} ORDER BY created ASC"
    rows = _jira.search_issues_for_analytics(jql)
    data = build_dashboard_data(rows)

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


_CHAT_REDIRECT_TARGETS = {"/chat", "/agent-console?level=L1"}


@app.post("/chat")
async def post_chat(message: str = Form(...), redirect_to: str = Form(default="/chat")):
    """`redirect_to` lets the embedded chat step in agent_console.html send
    the human back to the console (reopening on the chat step - see
    agent_console()'s docstring) instead of always landing on the
    standalone /chat page. Restricted to a fixed allowlist rather than
    trusted as-is - this is form input, not a hardcoded template value,
    even though only our own templates currently set it."""
    if redirect_to not in _CHAT_REDIRECT_TARGETS:
        redirect_to = "/chat"
    with _lock:
        session_id = _get_or_create_chat_session()
        history = _conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    history_dicts = [{"role": row["role"], "content": row["content"]} for row in history]

    try:
        answer, tool_calls = await run_chat_turn(history=history_dicts, message=message)
        outcome = "answered"
    except ChatError as exc:
        answer = f"Sorry, I couldn't answer that: {exc}"
        tool_calls = []
        outcome = "failed"

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
    return RedirectResponse(url=redirect_to, status_code=303)
