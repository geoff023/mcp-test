"""Tests for app/jobs.py (background runs with live progress) and the routes
that use it: POST /agent-console/start, POST /chat/start, GET /jobs/{id}.

The orchestrator and chat loops are stubbed here - tests/test_orchestrator.py
and tests/test_chat.py already prove the events they emit against a real
in-process gateway.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main
from app import jobs
from orchestrator.agent import OrchestratorError
from orchestrator.chat import ChatError


# ---------------------------------------------------------------- wording

def test_describe_step_uses_the_words_a_project_manager_would():
    assert jobs.describe_step("search_issues", {"jql": "project = X"}) == "Searching Jira issues"
    assert jobs.describe_step("get_issue", {"issue_key": "MCP-3"}) == "Reading MCP-3"
    assert jobs.describe_step("start_run", {}) == "Starting the run"
    assert jobs.describe_step("finish_run", {}) == "Finishing the run"
    assert jobs.describe_step("propose_estimate_change", {"issue_key": "MCP-3", "new_points": 13.0}) == "Proposing 13 points for MCP-3"
    assert jobs.describe_step("propose_estimate_change", {"issue_key": "MCP-3", "new_points": 1}) == "Proposing 1 point for MCP-3"
    assert jobs.describe_step("commit_changes", {}) == "Applying changes to Jira"
    assert jobs.describe_step("something_new", {}) == "Something new"  # never shows a raw code name


def test_describe_result_counts_search_hits_and_explains_refusals():
    assert jobs.describe_result("search_issues", True, {"result": [{}, {}, {}]}) == "3 issues found"
    assert jobs.describe_result("search_issues", True, {"result": [{}]}) == "1 issue found"
    assert jobs.describe_result("get_issue", True, {"key": "MCP-1"}) is None
    assert jobs.describe_result("propose_due_date_change", False, "MCP-4 is a milestone") == "MCP-4 is a milestone"
    assert jobs.describe_result("propose_due_date_change", False, None) == "Refused"
    assert jobs.describe_result("x", False, "y" * 300).endswith("...")


# ---------------------------------------------------------------- job lifecycle

def _tool(job: jobs.Job, state: str, tool: str, **extra) -> None:
    job.on_step({"phase": "tool", "state": state, "tool": tool, **extra})


def test_job_builds_its_steps_from_the_events_it_is_given():
    job = jobs.Job("run")

    job.on_step({"phase": "model", "state": "start"})
    assert job.thinking and job.steps == []

    job.on_step({"phase": "model", "state": "end"})
    _tool(job, "start", "get_issue", args={"issue_key": "MCP-2"})
    assert not job.thinking
    assert job.steps == [{"label": "Reading MCP-2", "state": "running", "detail": None}]

    _tool(job, "end", "get_issue", ok=True, result={})
    _tool(job, "start", "propose_due_date_change", args={"issue_key": "MCP-4"})
    _tool(job, "end", "propose_due_date_change", ok=False, result="MCP-4 is a milestone")

    assert [(s["label"], s["state"]) for s in job.steps] == [
        ("Reading MCP-2", "done"),
        ("Proposing a new due date for MCP-4", "failed"),
    ]
    assert job.steps[1]["detail"] == "MCP-4 is a milestone"


def test_the_number_of_steps_follows_what_the_agent_actually_did():
    short, long = jobs.Job("run"), jobs.Job("run")
    for tool in ("start_run", "finish_run"):
        _tool(short, "start", tool, args={})
        _tool(short, "end", tool, ok=True)
    for i in range(9):
        _tool(long, "start", "get_issue", args={"issue_key": f"MCP-{i}"})
        _tool(long, "end", "get_issue", ok=True)
    assert (len(short.steps), len(long.steps)) == (2, 9)


@pytest.mark.asyncio
async def test_start_runs_the_work_in_the_background_and_reports_where_to_go():
    async def work(job):
        _tool(job, "start", "get_issue", args={"issue_key": "MCP-1"})
        await asyncio.sleep(0)
        _tool(job, "end", "get_issue", ok=True)
        return "/runs/abc"

    job = jobs.start("run", work)
    assert job.status == "running"  # start() returned before the work finished

    await job.task

    assert (job.status, job.url, job.error) == ("done", "/runs/abc", None)
    assert job.steps[0]["state"] == "done"
    assert job.to_json()["thinking"] is False


@pytest.mark.asyncio
async def test_a_failure_written_for_humans_is_shown_as_is():
    async def work(job):
        raise OrchestratorError("Gemini never called start_run for this task at level='L1'.")

    job = jobs.start("run", work)
    await job.task

    assert job.status == "failed"
    assert job.error == "Gemini never called start_run for this task at level='L1'."


@pytest.mark.asyncio
async def test_an_unexpected_failure_does_not_leak_internals_to_the_page(capsys):
    async def work(job):
        raise RuntimeError("api key sk-secret-123 rejected")

    job = jobs.start("chat", work)
    await job.task

    assert job.status == "failed"
    assert "sk-secret" not in job.error
    assert "server log" in job.error
    assert "sk-secret-123" in capsys.readouterr().err  # the detail goes to the log instead


@pytest.mark.asyncio
async def test_only_the_most_recent_jobs_are_kept():
    async def work(job):
        return None

    made = [jobs.start("chat", work) for _ in range(jobs.MAX_JOBS + 5)]
    await asyncio.gather(*(j.task for j in made))

    assert len(jobs.JOBS) == jobs.MAX_JOBS
    assert made[0].id not in jobs.JOBS and made[-1].id in jobs.JOBS


# ---------------------------------------------------------------- routes

@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    captured: dict = {}

    def fake_start(kind, work):
        job = jobs.Job(kind)
        jobs.JOBS[job.id] = job
        captured.update(job=job, work=work, kind=kind)
        return job

    monkeypatch.setattr(app_main.jobs, "start", fake_start)
    return TestClient(app_main.app), captured


def test_starting_a_run_returns_a_job_id_straight_away_and_the_work_reports_its_steps(client, monkeypatch):
    test_client, captured = client
    seen: dict = {}

    async def fake_run(*, level, target_jql, instructions, on_step):
        seen.update(level=level, jql=target_jql, instructions=instructions)
        on_step({"phase": "tool", "state": "start", "tool": "start_run", "args": {}})
        on_step({"phase": "tool", "state": "end", "tool": "start_run", "ok": True, "result": "run-1"})
        return "run-1"

    monkeypatch.setattr(app_main, "run_reestimate_task", fake_run)

    resp = test_client.post(
        "/agent-console/start", data={"scope": "specific", "specific_issues": "MCP-4", "instructions": "be brief", "level": "L2"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"job_id": captured["job"].id}
    assert captured["kind"] == "run"

    url = asyncio.run(captured["work"](captured["job"]))

    assert url == "/runs/run-1"
    assert seen == {"level": "L2", "jql": "key in (MCP-4)", "instructions": "be brief"}
    assert captured["job"].steps[0]["label"] == "Starting the run"


def test_starting_a_run_at_l1_is_refused_because_l1_has_no_run(client):
    test_client, captured = client

    resp = test_client.post("/agent-console/start", data={"scope": "all", "level": "L1"})

    assert resp.status_code == 400
    assert "job" not in captured


def test_starting_a_run_with_an_unusable_scope_is_a_clear_400_not_a_started_job(client):
    test_client, captured = client

    resp = test_client.post("/agent-console/start", data={"scope": "specific", "specific_issues": "", "level": "L2"})

    assert resp.status_code == 400
    assert "job" not in captured


def test_job_status_returns_the_live_steps_and_404s_for_an_unknown_job(client):
    test_client, _ = client
    job = jobs.Job("run")
    jobs.JOBS[job.id] = job
    job.on_step({"phase": "tool", "state": "start", "tool": "get_issue", "args": {"issue_key": "MCP-2"}})

    body = test_client.get(f"/jobs/{job.id}").json()

    assert body["status"] == "running"
    assert body["steps"] == [{"label": "Reading MCP-2", "state": "running", "detail": None}]
    assert test_client.get("/jobs/nope").status_code == 404


def test_a_background_chat_turn_saves_the_thread_and_audit_row_like_the_blocking_route(client, conn, monkeypatch):
    test_client, captured = client

    async def fake_chat(*, history, message, on_step):
        on_step({"phase": "tool", "state": "start", "tool": "get_issue", "args": {"issue_key": "MCP-3"}})
        on_step({"phase": "tool", "state": "end", "tool": "get_issue", "ok": True, "result": {}})
        return "MCP-3 has 13 points.", ["get_issue({'issue_key': 'MCP-3'})"]

    monkeypatch.setattr(app_main, "run_chat_turn", fake_chat)

    resp = test_client.post("/chat/start", data={"message": "What is MCP-3?"})

    assert resp.json() == {"job_id": captured["job"].id}
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0  # nothing saved until it finishes

    asyncio.run(captured["work"](captured["job"]))

    rows = conn.execute("SELECT role, content FROM chat_messages ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("user", "What is MCP-3?"), ("agent", "MCP-3 has 13 points.")]
    assert conn.execute("SELECT outcome FROM audit_log WHERE action = 'chat_turn'").fetchone()[0] == "answered"
    assert captured["job"].steps[0]["label"] == "Reading MCP-3"


def test_a_background_chat_turn_that_fails_saves_the_apology_and_logs_the_failure(client, conn, monkeypatch):
    test_client, captured = client

    async def failing_chat(*, history, message, on_step):
        raise ChatError("too many tool calls")

    monkeypatch.setattr(app_main, "run_chat_turn", failing_chat)
    test_client.post("/chat/start", data={"message": "everything please"})

    asyncio.run(captured["work"](captured["job"]))

    saved = conn.execute("SELECT content FROM chat_messages WHERE role = 'agent'").fetchone()[0]
    assert "couldn't answer" in saved and "too many tool calls" in saved
    assert conn.execute("SELECT outcome FROM audit_log WHERE action = 'chat_turn'").fetchone()[0] == "failed"


def test_console_and_chat_pages_load_the_progress_script_with_a_cache_buster(client):
    test_client, _ = client

    for path in ("/agent-console", "/chat"):
        html = test_client.get(path).text
        assert "/static/progress.js?v=" in html
        assert 'class="progress"' in html
