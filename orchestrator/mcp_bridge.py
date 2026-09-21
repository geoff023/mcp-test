"""Shared MCP<->Gemini plumbing used by both orchestrator/agent.py (the
one-shot reestimate task) and orchestrator/chat.py (the L1 conversational
surface added in slice 4). Pulled out once a second caller needed the same
tool-schema conversion and result-unwrapping logic, rather than duplicating
gateway/server.py's own comments about MCP's structured_content wrapping in
two files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from google.genai import types as genai_types
from mcp import types as mcp_types

REPO_ROOT = Path(__file__).resolve().parent.parent


StepCallback = Callable[[dict], None]


def emit_step(on_step: StepCallback | None, **event: object) -> None:
    """Tell a listener what the loop is doing right now, so a UI can show it
    live. Events are {"phase": "model" | "tool", "state": "start" | "end", ...};
    tool events also carry the tool name, its args (start) and its result (end).
    Best-effort by design: a broken listener must never break the run itself.
    """
    if on_step is None:
        return
    try:
        on_step(event)
    except Exception as exc:  # noqa: BLE001 - see docstring
        print(f"progress listener failed: {exc!r}", file=sys.stderr)


def model_id() -> str:
    return os.environ.get("ORCHESTRATOR_MODEL", "gemini-3.6-flash")


def agent_identity(*, suffix: str | None = None) -> str:
    """The `agent` identity passed to build_server/GatewayTools - shows up
    as runs.agent and audit_log.actor. `suffix` distinguishes callers that
    share a model id but aren't the same kind of process (e.g. the L1 chat
    surface vs. the reestimate orchestrator)."""
    base = f"orchestrator:{model_id()}"
    return f"{base}:{suffix}" if suffix else base


def mcp_tools_to_gemini(tools: list[mcp_types.Tool]) -> genai_types.Tool:
    """Convert an MCP tool listing into Gemini function declarations.

    parameters_json_schema takes the tool's JSON Schema directly - MCP and
    Gemini both describe tool parameters as JSON Schema, so this is a
    pass-through, not a translation.
    """
    declarations = [
        genai_types.FunctionDeclaration(
            name=tool.name,
            description=tool.description or "",
            parameters_json_schema=tool.input_schema,
        )
        for tool in tools
    ]
    return genai_types.Tool(function_declarations=declarations)


def tool_result_payload(result: mcp_types.CallToolResult) -> object:
    """Prefer structured_content - it matches the tool's Python return type
    exactly (a dict for dict-returning tools, the bare string for str-
    returning ones like start_run). Falls back to the first text block for
    any result that somehow has none."""
    if result.structured_content is not None:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return None


def function_response_payload(call_name: str, is_error: bool, payload: object) -> dict:
    """What to hand back to Gemini as a function_response.response for one
    tool call. Mirrors gateway/server.py's own structured_content wrapping
    (see tool_result_payload above): a dict-shaped payload - wrapped or a
    tool's natural dict return alike - goes back as-is; wrapping it again
    here would double-nest it (caught by tests/test_orchestrator.py's
    scripted sequence, which re-parses exactly what a real model receives).
    """
    if is_error:
        return {"error": payload}
    if isinstance(payload, dict):
        return payload
    return {"result": payload}
