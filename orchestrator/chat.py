"""L1 (Consultant) conversational Q&A - added in slice 4, overriding
CLAUDE.md's original "no chat panel anywhere in this product" line (see
that file's dated addendum). Safe to add despite that original decision
because it changes nothing about the enforcement mechanism: this spawns
the gateway at AGENTIC_PM_LEVEL=L1 exactly like orchestrator/agent.py
spawns it at L2/L3/L4, so it structurally only ever gets search_issues and
get_issue (see gateway/server.py's TOOLS_BY_LEVEL). It cannot stage or
apply a change even if asked to - there is no tool for that at this level,
the same guarantee that holds for every other level in this system.

Multi-turn, but stateless per call: the caller (app/main.py) persists
`chat_messages` in the DB and passes the full history back in on every
turn, rather than this module holding any session state itself.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from orchestrator.mcp_bridge import (
    REPO_ROOT,
    agent_identity,
    function_response_payload,
    mcp_tools_to_gemini,
    model_id,
    tool_result_payload,
)

load_dotenv(REPO_ROOT / ".env")

CHAT_SYSTEM_PROMPT = """\
You are a read-only project-management assistant for Jira, answering a human's questions \
about their project. You have exactly two tools - search_issues and get_issue - and nothing \
else: no way to change, propose, or apply anything, structurally, no matter how you are \
asked. If asked to change something, say plainly that you cannot from here, and that they \
should use the Agent console's Co-worker, Committer, or Super-Pilot modes instead, or make the \
change in Jira directly. Answer only from what search_issues/get_issue actually return for \
this request - never invent an issue key, status, or value you have not just read. Be \
concise; this is a chat reply, not a report."""

MAX_TOOL_CALLS = 10
# A chat answer needing more read calls than this almost certainly means the
# question was too broad for one turn, not that the assistant is making
# progress - fail with a clear message rather than loop.


class ChatError(Exception):
    """Raised when a chat turn can't produce a final answer - a Gemini/MCP
    failure, or MAX_TOOL_CALLS reached without the model producing text."""


async def run_chat_turn(
    *,
    history: list[dict],
    message: str,
    gemini: genai.Client | None = None,
    mcp_server: object | None = None,
) -> tuple[str, list[str]]:
    """Runs one L1 chat turn and returns (answer_text, tool_calls_made).

    `history` is a list of {"role": "user" | "agent", "content": str}
    dicts, oldest first - the prior turns of this conversation, replayed
    into Gemini's `contents` so each turn has the full context. `gemini`
    and `mcp_server` are injectable for tests, the same pattern
    orchestrator/agent.py uses.
    """
    if mcp_server is None:
        mcp_server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gateway.server"],
            cwd=str(REPO_ROOT),
            env={
                **os.environ,
                "AGENTIC_PM_LEVEL": "L1",
                "AGENTIC_PM_AGENT": agent_identity(suffix="chat"),
            },
        )

    gemini_client = gemini or genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = model_id()

    async with Client(mcp_server) as mcp_client:
        tools_result = await mcp_client.list_tools()
        config = genai_types.GenerateContentConfig(
            system_instruction=CHAT_SYSTEM_PROMPT,
            tools=[mcp_tools_to_gemini(tools_result.tools)],
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )

        contents: list[genai_types.Content] = [
            genai_types.Content(
                role="user" if turn["role"] == "user" else "model",
                parts=[genai_types.Part.from_text(text=turn["content"])],
            )
            for turn in history
        ]
        contents.append(genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=message)]))

        tool_calls_made: list[str] = []
        calls_made = 0
        final_text: str | None = None

        while calls_made < MAX_TOOL_CALLS:
            response = await gemini_client.aio.models.generate_content(model=model, contents=contents, config=config)
            candidate = response.candidates[0]
            contents.append(candidate.content)

            function_calls = [p.function_call for p in candidate.content.parts if p.function_call is not None]
            if not function_calls:
                final_text = "".join(p.text for p in candidate.content.parts if p.text) or None
                break

            response_parts: list[genai_types.Part] = []
            for call in function_calls:
                calls_made += 1
                tool_calls_made.append(f"{call.name}({dict(call.args or {})})")
                result = await mcp_client.call_tool(call.name, dict(call.args or {}))
                payload = tool_result_payload(result)
                response_payload = function_response_payload(call.name, result.is_error, payload)
                response_parts.append(
                    genai_types.Part.from_function_response(name=call.name, response=response_payload)
                )

            contents.append(genai_types.Content(role="user", parts=response_parts))

    if final_text is None:
        raise ChatError(
            "The assistant used its read-only tools but did not produce a final answer "
            f"within {MAX_TOOL_CALLS} tool calls - try a narrower question."
        )
    return final_text, tool_calls_made
