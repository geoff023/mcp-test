"""Tests 1-2 from docs/slice-3.md section 12: the orchestrator's tool-use
loop, and the L1 regression guard - proven against the real MCP dispatch
and tool-registration logic (an in-process MCPServer built the same way
build_server() always builds one), with only the LLM itself faked. The
LLM call is non-deterministic and costs money; it does not belong in the
suite that runs on every `pytest` invocation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import types as genai_types

from gateway.server import build_server
from orchestrator.agent import OrchestratorError, run_reestimate_task


class _FakeModels:
    """Scripts a fixed tool-call sequence: search_issues -> start_run ->
    propose_estimate_change -> finish_run. run_id for the later two calls
    is read back out of start_run's own function_response in the growing
    conversation - the same way a real model would - rather than
    hardcoded, since it's a fresh uuid each test run."""

    def __init__(self):
        self._step = 0

    async def generate_content(self, *, model, contents, config):
        run_id = self._find_run_id(contents)
        steps = [
            lambda: genai_types.Part.from_function_call(name="search_issues", args={"jql": "project = TEST"}),
            lambda: genai_types.Part.from_function_call(
                name="start_run", args={"task_type": "reestimate", "scope": "project = TEST"}
            ),
            lambda: genai_types.Part.from_function_call(
                name="propose_estimate_change",
                args={
                    "run_id": run_id,
                    "issue_key": "TEST-1",
                    "new_points": 3,
                    "reasoning": "Scripted test reasoning long enough to pass validation.",
                    "assumptions": "NA",
                },
            ),
            lambda: genai_types.Part.from_function_call(name="finish_run", args={"run_id": run_id}),
        ]
        if self._step < len(steps):
            part = steps[self._step]()
            self._step += 1
        else:
            part = genai_types.Part.from_text(text="Done.")
        content = genai_types.Content(role="model", parts=[part])
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    @staticmethod
    def _find_run_id(contents) -> str | None:
        for c in contents:
            for p in getattr(c, "parts", None) or []:
                fr = getattr(p, "function_response", None)
                if fr is not None and fr.name == "start_run":
                    result = (fr.response or {}).get("result")
                    return result if isinstance(result, str) else None
        return None


class _StallingModels:
    """Never calls a tool - simulates a model that, given only read tools
    (L1), has nothing it can do to complete the task."""

    async def generate_content(self, *, model, contents, config):
        content = genai_types.Content(
            role="model",
            parts=[genai_types.Part.from_text(text="I don't have a tool that lets me do this.")],
        )
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _fake_gemini(models) -> SimpleNamespace:
    return SimpleNamespace(aio=SimpleNamespace(models=models))


@pytest.mark.asyncio
async def test_orchestrator_drives_the_real_gateway_through_a_scripted_sequence(conn, jira_with_spy):
    jira, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}  # search_issues' first call
    server = build_server("L3", jira=jira, conn=conn, agent="orchestrator:fake-model")

    run_id = await run_reestimate_task(
        level="L3",
        target_jql="project = TEST",
        instructions="",
        gemini=_fake_gemini(_FakeModels()),
        mcp_server=server,
    )

    assert run_id is not None
    row = conn.execute("SELECT status, agent FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row == ("awaiting_review", "orchestrator:fake-model")

    change = conn.execute(
        "SELECT issue_key, new_value FROM staged_changes WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert change == ("TEST-1", "3.0")  # new_points is typed float; pydantic coerces 3 -> 3.0


@pytest.mark.asyncio
async def test_orchestrator_at_l1_fails_cleanly_with_no_write_tools(conn, jira_with_spy):
    jira, _ = jira_with_spy
    server = build_server("L1", jira=jira, conn=conn, agent="orchestrator:fake-model")

    with pytest.raises(OrchestratorError, match="never called start_run"):
        await run_reestimate_task(
            level="L1",
            target_jql="project = TEST",
            instructions="",
            gemini=_fake_gemini(_StallingModels()),
            mcp_server=server,
        )

    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
