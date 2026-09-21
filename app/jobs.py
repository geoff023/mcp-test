"""Background jobs with live progress: an agent run or a chat turn that keeps
going after the HTTP request that started it has returned, while the page
polls GET /jobs/{id} and draws each step as it happens.

Deliberately in-memory and single-process (a dict, no queue, no DB table):
this is a one-user research prototype, and a job only matters while its page
is open - the run's real record (runs, staged_changes, audit_log) is written
by the gateway as always. Restarting the server forgets in-flight jobs, and
the run page already tells you a still-'running' run needs a refresh.

The steps shown are the orchestrator's real tool calls, translated into plain
language - nothing here is scripted or invented, so a run that needs two
calls shows two steps and one that needs twenty shows twenty.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.run_display import humanize
from orchestrator.agent import OrchestratorError
from orchestrator.chat import ChatError

MAX_JOBS = 50
_EXPECTED_ERRORS = (OrchestratorError, ChatError)  # messages written for humans
_UNEXPECTED = "The agent hit an unexpected error. The server log has the details."


def _points(value: object) -> str:
    text = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    return f"{text} point" if text == "1" else f"{text} points"


def describe_step(tool: str, args: dict) -> str:
    """One tool call, in the words a project manager would use."""
    key = args.get("issue_key")
    if tool == "search_issues":
        return "Searching Jira issues"
    if tool == "get_issue":
        return f"Reading {key}" if key else "Reading an issue"
    if tool == "start_run":
        return "Starting the run"
    if tool == "propose_estimate_change":
        return f"Proposing {_points(args.get('new_points'))} for {key}" if key else "Proposing an estimate"
    if tool == "propose_due_date_change":
        return f"Proposing a new due date for {key}" if key else "Proposing a new due date"
    if tool == "finish_run":
        return "Finishing the run"
    if tool == "commit_changes":
        return "Applying changes to Jira"
    return humanize(tool)


def describe_result(tool: str, ok: bool, result: object) -> str | None:
    """A short note once a step is done: how many issues a search found, or why
    the gateway refused a call (a guardrail refusal is exactly what a reviewer
    wants to see)."""
    if not ok:
        text = str(result or "").strip()
        return (text[:140] + "...") if len(text) > 140 else (text or "Refused")
    if tool == "search_issues":
        items = result.get("result") if isinstance(result, dict) else result
        if isinstance(items, list):
            return f"{len(items)} {'issue' if len(items) == 1 else 'issues'} found"
    return None


@dataclass
class Job:
    kind: str  # "run" | "chat"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "running"  # running | done | failed
    steps: list[dict] = field(default_factory=list)
    thinking: bool = False  # waiting on the model between tool calls
    url: str | None = None  # where the page goes when the job is done
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def on_step(self, event: dict) -> None:
        """The orchestrator's progress callback (see mcp_bridge.emit_step)."""
        if event["phase"] == "model":
            self.thinking = event["state"] == "start"
        elif event["state"] == "start":
            label = describe_step(event["tool"], event.get("args") or {})
            self.steps.append({"label": label, "state": "running", "detail": None})
        elif self.steps:
            ok = event.get("ok", True)
            step = self.steps[-1]  # tool calls run one at a time, so it is the last
            step["state"] = "done" if ok else "failed"
            step["detail"] = describe_result(event["tool"], ok, event.get("result"))

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "steps": self.steps,
            "thinking": self.thinking and self.status == "running",
            "url": self.url,
            "error": self.error,
        }


JOBS: dict[str, Job] = {}


def start(kind: str, work: Callable[[Job], Awaitable[str | None]]) -> Job:
    """Runs `work(job)` in the background and returns the job straight away.
    `work` returns the URL to send the page to when it finishes (or None)."""
    job = Job(kind)
    JOBS[job.id] = job
    while len(JOBS) > MAX_JOBS:
        JOBS.pop(next(iter(JOBS)))
    job.task = asyncio.get_running_loop().create_task(_drive(job, work))
    return job


async def _drive(job: Job, work: Callable[[Job], Awaitable[str | None]]) -> None:
    try:
        job.url = await work(job)
        job.status = "done"
    except _EXPECTED_ERRORS as exc:
        job.error, job.status = str(exc), "failed"
    except (Exception, BaseExceptionGroup):  # anyio wraps stdio failures in a group
        traceback.print_exc(file=sys.stderr)
        job.error, job.status = _UNEXPECTED, "failed"
    finally:
        job.thinking = False
