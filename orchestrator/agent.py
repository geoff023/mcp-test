"""The orchestrator: connects to gateway/server.py as a real MCP client -
the same way Claude Code does, over the same protocol, not a Python
backdoor into GatewayTools - and drives a re-estimate run via Gemini's
tool-use loop instead of a human typing a chat prompt. See docs/slice-3.md
sections 3 and 8.

gateway/server.py needed no changes for this to work (beyond slice 3's
own agent-parameter refactor, unrelated to this file): this is a second
*caller*, not a new code path. It can structurally only call whatever
list_tools() returns for the level it was started at, exactly like Claude
Code - list_tools() is driven by the same TOOLS_BY_LEVEL table either way.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from orchestrator.mcp_bridge import (
    REPO_ROOT,
    agent_identity,
    emit_step,
    function_response_payload,
    mcp_tools_to_gemini,
    model_id,
    tool_result_payload,
)
from orchestrator.prompts import REESTIMATE_SYSTEM_PROMPT, build_task_prompt

_REPO_ROOT = REPO_ROOT
load_dotenv(_REPO_ROOT / ".env")

MAX_TOOL_CALLS = 80
# Sized for realistic scopes, not just the 1-4 issue demo runs: each issue can
# cost up to 2 calls (get_issue for detail, then propose_estimate_change), plus
# search_issues + start_run + finish_run overhead. 20 was enough for the demo
# scripts but failed live on "all stories in this project" (9 issues, ~21+
# calls needed) - the run wasn't lost (every proposal it had already staged
# stayed in the DB), but finish_run was never reached, so the run itself
# never left 'running'. 80 covers dozens of issues in one run; a scope with
# genuinely hundreds of issues would need this raised further, or the task
# split into smaller scopes - not attempting a dynamic per-scope budget yet.
MAX_STALLS = 2
CONTINUE_NUDGE = (
    "Continue the task - you have not called finish_run yet. Call the next "
    "appropriate tool rather than stopping or asking a question; there is no "
    "human available to answer one mid-task."
)


class OrchestratorError(Exception):
    """Raised when the orchestrator can't complete the task.

    Deliberately not a per-level check the orchestrator makes about itself
    - e.g. "L1 has no start_run" is discovered the same way any bug would
    be: the tool isn't there to call, Gemini can't call it, run_id stays
    None. Re-implementing that as an explicit guard here would be exactly
    the "ask the model to please behave" pattern CLAUDE.md warns against -
    see docs/slice-3.md section 3.
    """


async def run_reestimate_task(
    *,
    level: str,
    target_jql: str,
    instructions: str,
    gemini: genai.Client | None = None,
    mcp_server: object | None = None,
    on_step: Callable[[dict], None] | None = None,
) -> str:
    """Spawns the gateway at `level` as a subprocess, drives a full
    re-estimate run via Gemini's tool use, and returns the run_id once
    finish_run has been called.

    `gemini` and `mcp_server` are injectable for tests, the same pattern
    GatewayTools/build_server already use for jira/conn: `mcp_server` can
    be any `mcp.client.Client`-compatible target - an in-process
    `MCPServer` instance (what tests use, built via build_server() with
    fake jira/conn) or another `StdioServerParameters` - so a test can
    drive the real MCP dispatch/registration logic without spawning a
    subprocess or hitting real Jira. Production leaves both as the
    default: a real Gemini client and a real gateway subprocess.

    `on_step`, if given, is called with a small event dict as each model call
    and each tool call starts and ends (see mcp_bridge.emit_step) - that is how
    the web UI shows a run's real steps live. It never affects the run.

    Raises OrchestratorError if Gemini never calls start_run at all (most
    likely because `level` has no write tools - L1), or stages proposals
    but never calls finish_run within MAX_TOOL_CALLS tool calls.
    """
    if mcp_server is None:
        mcp_server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gateway.server"],
            cwd=str(_REPO_ROOT),
            env={
                **os.environ,
                "AGENTIC_PM_LEVEL": level,
                "AGENTIC_PM_AGENT": agent_identity(),
            },
        )

    gemini_client = gemini or genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = model_id()

    async with Client(mcp_server) as mcp_client:
        tools_result = await mcp_client.list_tools()
        config = genai_types.GenerateContentConfig(
            system_instruction=REESTIMATE_SYSTEM_PROMPT,
            tools=[mcp_tools_to_gemini(tools_result.tools)],
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )

        contents: list[genai_types.Content] = [
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(
                        text=build_task_prompt(target_jql=target_jql, instructions=instructions)
                    )
                ],
            )
        ]

        run_id: str | None = None
        finished = False
        calls_made = 0
        stalls = 0

        while calls_made < MAX_TOOL_CALLS:
            emit_step(on_step, phase="model", state="start")
            response = await gemini_client.aio.models.generate_content(model=model, contents=contents, config=config)
            emit_step(on_step, phase="model", state="end")
            candidate = response.candidates[0]
            contents.append(candidate.content)

            function_calls = [p.function_call for p in candidate.content.parts if p.function_call is not None]
            if not function_calls:
                # Gemini paused without calling finish_run - happens often enough in
                # practice (it sometimes summarises after just reading, before acting)
                # that treating the first pause as final would make the orchestrator
                # unreliable for no good reason. Nudge it to continue, bounded by
                # MAX_STALLS so a model that's genuinely stuck still fails loudly
                # rather than looping forever.
                if finished or stalls >= MAX_STALLS:
                    break
                stalls += 1
                contents.append(
                    genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=CONTINUE_NUDGE)])
                )
                continue

            response_parts: list[genai_types.Part] = []
            for call in function_calls:
                calls_made += 1
                args = dict(call.args or {})
                emit_step(on_step, phase="tool", state="start", tool=call.name, args=args)
                result = await mcp_client.call_tool(call.name, args)
                payload = tool_result_payload(result)
                emit_step(on_step, phase="tool", state="end", tool=call.name, ok=not result.is_error, result=payload)

                if call.name == "start_run" and not result.is_error and run_id is None:
                    # start_run returns a bare str in Python, but MCP wraps a
                    # non-object structured_content as {"result": <value>} on
                    # protocol versions that require a JSON object (SEP-2686) -
                    # confirmed empirically, not "run_id" as the field name
                    # might suggest. A real model reads the value out of
                    # context regardless of the key name and isn't tripped up
                    # by this; our own bookkeeping was, silently, until
                    # tests/test_orchestrator.py's scripted (non-LLM) sequence
                    # caught it.
                    run_id = payload if isinstance(payload, str) else (payload or {}).get("result")
                if call.name == "finish_run" and not result.is_error:
                    finished = True

                # See orchestrator/mcp_bridge.function_response_payload for
                # why a dict-shaped payload goes back as-is rather than
                # wrapped again - double-wrapping produced
                # {"result": {"result": ...}}, which the scripted test in
                # tests/test_orchestrator.py caught by re-parsing exactly
                # what a real model receives; Gemini itself shrugged it off
                # by reading the value out semantically regardless of
                # nesting.
                response_payload = function_response_payload(call.name, result.is_error, payload)

                response_parts.append(
                    genai_types.Part.from_function_response(name=call.name, response=response_payload)
                )

            contents.append(genai_types.Content(role="user", parts=response_parts))

            if finished:
                break
        # `async with Client(...)` ends here. The two checks below run only
        # after that block has exited cleanly - raising from inside it would
        # have the OrchestratorError wrapped in a BaseExceptionGroup by
        # anyio's task-group cleanup (the stdio transport runs one
        # internally), which a plain `except OrchestratorError` upstream
        # cannot catch. Learned the hard way: app/main.py's route caught
        # nothing and surfaced a 500 instead of the intended 502 until this
        # was moved out here.

    if run_id is None:
        raise OrchestratorError(
            f"Gemini never called start_run for this task at level={level!r} - "
            "check that this level actually has write tools."
        )
    if not finished:
        raise OrchestratorError(
            f"Gemini staged proposals for run {run_id} but never called finish_run "
            f"within {MAX_TOOL_CALLS} tool calls."
        )
    return run_id
