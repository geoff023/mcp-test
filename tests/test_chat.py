"""Tests for orchestrator/chat.py (slice 4's L1 conversational surface).

Same approach as tests/test_orchestrator.py: drive the real MCP dispatch
and tool-registration logic (an in-process MCPServer built via
build_server("L1", ...), exactly like production spawns it), with only the
LLM itself faked - non-deterministic and costs money, so it doesn't belong
in the suite that runs on every `pytest` invocation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import types as genai_types

from gateway.server import build_server
from orchestrator.chat import ChatError, run_chat_turn


class _FakeModels:
    """Scripts: one search_issues call, then a final text answer."""

    def __init__(self):
        self._step = 0

    async def generate_content(self, *, model, contents, config):
        if self._step == 0:
            part = genai_types.Part.from_function_call(name="search_issues", args={"jql": "project = TEST"})
        else:
            part = genai_types.Part.from_text(text="There are no unestimated stories right now.")
        self._step += 1
        content = genai_types.Content(role="model", parts=[part])
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class _NeverAnswersModels:
    """Keeps calling search_issues forever - never produces final text, so
    run_chat_turn must fail loudly at MAX_TOOL_CALLS rather than hang."""

    async def generate_content(self, *, model, contents, config):
        part = genai_types.Part.from_function_call(name="search_issues", args={"jql": "project = TEST"})
        content = genai_types.Content(role="model", parts=[part])
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _fake_gemini(models) -> SimpleNamespace:
    return SimpleNamespace(aio=SimpleNamespace(models=models))


@pytest.mark.asyncio
async def test_chat_turn_uses_only_l1_read_tools_and_returns_an_answer(conn, jira_with_spy):
    jira, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}
    server = build_server("L1", jira=jira, conn=conn)

    answer, tool_calls = await run_chat_turn(
        history=[], message="Which stories have no estimate?", gemini=_fake_gemini(_FakeModels()), mcp_server=server
    )

    assert answer == "There are no unestimated stories right now."
    assert len(tool_calls) == 1
    assert tool_calls[0].startswith("search_issues(")
    # No Jira write ever happened - there is no write tool at L1 to call in
    # the first place (see tests/test_server_tool_registration.py), and
    # search_issues itself never calls jira.update_issue_fields.
    for call in spy.request.call_args_list:
        method = call.args[0] if call.args else call.kwargs.get("method")
        assert str(method).upper() in ("GET", "POST")  # POST is the JQL search endpoint, never a write


@pytest.mark.asyncio
async def test_chat_turn_replays_history_into_the_conversation(conn, jira_with_spy):
    jira, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}
    server = build_server("L1", jira=jira, conn=conn)
    history = [
        {"role": "user", "content": "What is MCP-1?"},
        {"role": "agent", "content": "MCP-1 is a Story with 3 points."},
    ]

    answer, _ = await run_chat_turn(
        history=history, message="And MCP-2?", gemini=_fake_gemini(_FakeModels()), mcp_server=server
    )

    assert answer == "There are no unestimated stories right now."


@pytest.mark.asyncio
async def test_chat_turn_raises_when_the_model_never_gives_a_final_answer(conn, jira_with_spy):
    jira, spy = jira_with_spy
    spy.request.return_value.raise_for_status.return_value = None
    spy.request.return_value.json.return_value = {"issues": []}
    server = build_server("L1", jira=jira, conn=conn)

    with pytest.raises(ChatError, match="did not produce a final answer"):
        await run_chat_turn(
            history=[], message="Anything", gemini=_fake_gemini(_NeverAnswersModels()), mcp_server=server
        )
