# Slice 3 — the orchestrator

**Built and fully verified live.** All decisions resolved; sections 7-9, 11, and 12 built and
tested (29 tests passing). Billing enabled on the Gemini project 2026-09-08 cleared the
free-tier quota block; both outstanding live checks then passed for real: `/agent-console`
drove a real Gemini call through the real gateway (MCP-10 -> 2.0 pts, confidence 0.85) and
approving it changed Jira for real, and `scripts/demo_slice3.py` ran the full six-step
narration against the real site (MCP-7 -> 3.0 pts). Every DoD item in section 13 is checked
off. Follows the same format as docs/slice-1.md and
docs/slice-2.md. Supersedes the "Slice 3 preview" note at the bottom of docs/slice-2.md —
that note's reasoning is folded in here, not repeated.

**Goal:** replace "a human types a prompt into Claude Code" with "a human fills in a form and
an LLM drives the run" — for exactly one task type, at whatever level the human picks. This is
the "dedicated orchestrator" slice 1 always said was coming later. Everything downstream of
`start_run` (staging, guardrails, the review surface, level gating) is already built and does
not change; only *what calls the propose tools* changes.

**Estimated size:** 350–550 lines. Bigger than slice 2 — this is the first slice adding a
genuinely new component (an LLM tool-use loop), not just extending the existing one.

---

## 1. In scope

- One task type: **re-estimate** (the existing `reestimate` task — "Effort Estimation" in the
  UI reference). See `[DECIDE]` below for why not a new task type.
- A real LLM (Gemini, via Google's `google-genai` SDK) driving the run by calling the **same
  MCP tools** Claude Code has been calling by hand, over the **same MCP protocol** — not a
  Python backdoor into `GatewayTools`. See `[DECIDE]` below for why this distinction matters.
  Provider choice resolved 2026-09-08: Gemini instead of Claude, deliberately — it's a
  stronger research point that the tool-layer enforcement holds regardless of which LLM is
  driving, not just Claude.
- A single-page "define and run" form: target JQL, free-text instructions, working mode
  (level) selector, Run button. Not the full 4-step wizard chrome from the mockup — see
  `[DECIDE]`.
- Handing off to the **existing** `/runs/{run_id}` review surface once the run reaches
  `awaiting_review` (or fails).
- An `awaiting_review` count on `GET /`, matching the mockup's "Approvals" badge.

## 2. Explicitly out of scope

The other seven task types from the mockup (several of which conflict with CLAUDE.md's
stated non-goals — meeting recorders, email sending — regardless of orchestrator work).
No scheduling/cron triggers — a human still clicks Run. No background job queue or live
progress streaming — the request blocks until the run reaches `awaiting_review`. No new
guardrail. No change to L1, L2, or L3's existing behaviour once a run exists — an
orchestrator-created run is reviewed exactly like a Claude-Code-created one.

---

## 3. `[RESOLVED]` — real MCP client, not a Python backdoor

The orchestrator could call `GatewayTools` methods directly in-process (fast to build), or it
could connect to `gateway/server.py` as an actual MCP client over stdio — the same way Claude
Code does — and only ever see the tools `build_server(level)` registered for it.

**These are not equivalent, and it matters for this specific project.** Level gating today is
enforced entirely at MCP registration (`TOOLS_BY_LEVEL` in `gateway/server.py`) — nothing
inside `GatewayTools.commit_changes` itself checks the level. A caller that imports
`GatewayTools` directly and calls `.commit_changes(...)` can call it at any level; the
enforcement only exists because the *only* callers so far (Claude Code, this app) go through
`build_server()`. An in-process orchestrator would be a second caller that bypasses that
boundary — CLAUDE.md's central claim, "autonomy is enforced by the tool layer, not the
prompt," would quietly stop being true for this caller unless the orchestrator re-implements
its own level check, which is exactly the "ask the model to please behave" pattern CLAUDE.md
says to avoid.

**Recommendation: real MCP client.** The orchestrator spawns `gateway/server.py` as a
subprocess (via the `mcp` SDK's stdio client, same package already a dependency), calls
`list_tools()`, and can structurally only call what came back. This is more code than the
backdoor, but it's the version that actually demonstrates the thesis with a second, automated
caller — which is the whole point of this slice existing.

---

## 4. `[RESOLVED]` — Gemini, not Anthropic

The orchestrator needs an LLM that can call tools. Originally proposed as the Anthropic
Messages API (natural fit for a Claude Code project); **changed to Gemini on request**
(`google-genai` Python package, `GEMINI_API_KEY` + `ORCHESTRATOR_MODEL` in `.env`/
`.env.example`).

This is still the first thing in the whole project that spends real money per run and needs a
second secret alongside the Jira token — that part of the flag stands regardless of provider.

Verified live: the key works, but this account's free tier has zero quota for the `2.5-*` and
`3.1-pro-*` model families (`404`/`429` from the API, not an auth failure) — the API's own
error message pointed at current model names. Landed on **`gemini-3.6-flash`**, confirmed
with a real `generate_content` call. If quota errors show up again later, re-run
`client.models.list()` and check `supported_actions` for `generateContent` rather than
guessing a model name.

## 5. `[RESOLVED]` — which task type first

Already reasoned through in the slice-2.md preview note; confirmed by proceeding:

- **Effort Estimation** — the existing `reestimate` flow, proven at all four levels already.
  Zero new Jira surface, zero new guardrail. This is what's built below.
- **Sprint Planning** — would need the Agile API and reopens "no sprint manipulation," which
  has been explicitly out of scope since slice 1. A reasonable *second* orchestrator task,
  not the first.

---

## 6. Architecture for this slice

```
   Browser (control plane UI)
        │  fills in task form, clicks Run
        ▼
   FastAPI app  ──POST /agent-console/run──┐
        │                                   │
        │ blocks until the run reaches      ▼
        │ awaiting_review or fails    orchestrator/agent.py
        │                                   │  spawns as MCP client (stdio)
        ▼                                   ▼
   redirects to                     gateway/server.py (unchanged)
   /runs/{run_id}                          │
        │                                   │ tools scoped by level, same as always
        ▼                                   ▼
   SQLite  ◄── staged_changes ────────────  │
        ▲                                   ▼
        │                            Jira Cloud REST API
        │
   Gemini API (google-genai)
   (tool-use loop: search_issues, get_issue,
    start_run, propose_estimate_change, finish_run)
```

The orchestrator is a **second MCP client**, structurally identical in what it can do to
Claude Code — it just isn't a human-driven chat session. `gateway/server.py` needs no new
tools and no awareness that a different kind of caller now exists.

---

## 7. `[DONE]` `gateway/server.py` change: `agent` becomes a parameter, not a constant

`AGENT_NAME = "planning"` was a module constant, so every run's `agent` column and every
audit row's `actor` said `"agent:planning"` regardless of who was actually driving it. Once
an orchestrator exists, "who proposed this" stops being a settled fact and the audit trail
needs to say so - this was the provenance gap the slice-2.md preview flagged.

**Built.** `GatewayTools.__init__` now takes an `agent: str = DEFAULT_AGENT` parameter (
`DEFAULT_AGENT = "planning"`); `.actor` is an instance property (`f"agent:{self.agent}"`)
instead of a module constant. `build_server()` threads `agent` through; `main()` reads a new
`AGENTIC_PM_AGENT` env var (default `"planning"`) the same way `AGENTIC_PM_LEVEL` already
works. Claude-Code-driven runs are unaffected (still default to `"agent:planning"`) - all 23
existing tests passed unmodified. The orchestrator will spawn its gateway subprocess with
`AGENTIC_PM_AGENT=orchestrator:<model>` (e.g. `orchestrator:gemini-3.6-flash`) set in its
environment. No schema change needed - `runs.agent` and `audit_log.actor` already exist as
free-text columns.

---

## 8. `[DONE]` `orchestrator/` — the new module

```
orchestrator/
  __init__.py
  agent.py      Connects to the gateway as an MCP client, runs the Gemini tool-use
                loop, drives start_run -> ... -> finish_run, returns the run_id.
  prompts.py    The system prompt for the reestimate task. One prompt, not a per-task-type
                registry - there's only one task type this slice.
```

`agent.py`'s shape:

```python
async def run_reestimate_task(
    *, level: str, target_jql: str, instructions: str,
    gemini: genai.Client | None = None, mcp_server: object | None = None,
) -> str:
    """Drives a full re-estimate run via Gemini's tool use, returns the run_id once
    finish_run has been called (or raises if the model never gets there)."""
```

- Connects via the MCP stdio client by default (`AGENTIC_PM_LEVEL=level` and
  `AGENTIC_PM_AGENT=orchestrator:<model>` in the subprocess environment - same mechanism
  `.mcp.json` already uses for level; agent is new, see section 7). `gemini`/`mcp_server` are
  injectable, the same DI pattern `build_server(jira=, conn=)` already uses - tests pass an
  in-process `MCPServer` (built by `build_server()` with the fake jira/conn fixtures) and a
  scripted fake model, so the real MCP dispatch/registration logic gets exercised without a
  subprocess or real Jira. Not something planned up front - added once test 1 needed it.
- Converts the MCP `list_tools()` result into Gemini's function-declaration schema via
  `parameters_json_schema` - the tool's JSON Schema passed straight through, not translated.
  Uses the manual tool-calling loop (`generate_content` with explicit `tools=[...]`,
  automatic function calling disabled), not Gemini's automatic mode - automatic mode needs a
  real Python callable per tool, which is exactly what the MCP boundary is for us *not* to
  hand out directly.
- System prompt (`prompts.py`) states the task, the target JQL, and the human's free-text
  instructions; tells the model to call `search_issues`/`get_issue` to inform its estimates,
  then `start_run`, one `propose_estimate_change` per issue with real reasoning, then
  `finish_run`.
- Loop cap (`MAX_TOOL_CALLS = 80`, raised from an initial 20 after a real "all stories in this
  project" run hit it live at 9 issues - see the bugs list below) so a confused model can't
  run away. Separately, Gemini
  pausing without calling a tool (observed live: happens often enough that treating the first
  pause as final made the orchestrator unreliable for no good reason - it would sometimes
  summarise right after reading, before acting) gets a bounded "continue" nudge
  (`MAX_STALLS = 2`) rather than an immediate bail-out.
- Captures the run_id from `start_run`'s tool result the first time the model calls it.

### Three real bugs/gaps this surfaced, worth keeping for the report

1. **`CallToolResult.structured_content` wraps non-object returns.** A Python `str`/`list`
   return (like `start_run`'s run_id, or `search_issues`' list) doesn't satisfy "structured
   content must be a JSON object" on some protocol versions, so the SDK wraps it as
   `{"result": <value>}` - confirmed empirically, not documented anywhere obvious. Cost two
   bugs: reading the run_id back out under the wrong key (`"run_id"` instead of `"result"`),
   and then wrapping an *already-wrapped* dict again when constructing the function-response
   sent back to the model (`{"result": {"result": <value>}}`). Gemini itself never noticed
   either bug - a real model reads a value out of JSON semantically regardless of the key
   name or nesting - but `tests/test_orchestrator.py`'s scripted, deterministic sequence
   (which has to construct its next call from the literal prior response) failed loudly on
   both. The scripted test caught something four separate live runs against the real API
   didn't, because the real model was smart enough to route around the bug.
2. **Raising from inside `async with Client(...)` gets wrapped in a `BaseExceptionGroup`** by
   anyio's task-group cleanup (the stdio transport runs one internally), which a plain
   `except OrchestratorError` upstream doesn't catch - `app/main.py`'s route surfaced a 500
   instead of the intended 502 until the raise was moved to after the `async with` block exits.
3. **`MAX_TOOL_CALLS = 20` was sized for the demo scripts (1-4 issues), not real usage.**
   Found live, not in testing: a user picked "all stories in this project" (9 issues) at L4
   and hit the cap before `finish_run` - Gemini had already genuinely staged real proposals
   (with real reasoning) for all 9 issues, but the run stayed stuck at `status = 'running'`
   since it never got there. The staged work wasn't lost (it's real rows in `staged_changes`,
   recovered by hand-flipping that one run to `awaiting_review`), but the run couldn't be
   acted on through the UI until that recovery. Raised to 80. Worth noting for the report:
   this is a real limit of the "no background job queue" decision in section 2 combined with
   a fixed per-run budget - a genuinely large scope will eventually need either a much higher
   cap, a dynamic budget sized to the actual issue count, or the task split into batches.

## 9. `[DONE]` The web app

### New route

```
GET  /agent-console                the define-and-run form
POST /agent-console/run            runs the orchestrator synchronously, redirects to
                                    /runs/{run_id} on success, 502 with the OrchestratorError
                                    message on failure
```

Form fields: target JQL (text input), instructions (textarea, optional), working mode
(select: L2/L3/L4 - **L1 deliberately left out of the dropdown**, since it structurally
cannot complete this task and offering it would just be a guaranteed-failure option; the L1
boundary is still covered by `tests/test_orchestrator.py`'s direct call). Task type is
fixed/displayed as "Effort Estimation" - not a live choice yet, per the in-scope note above.
No JS, no build step, consistent with the rest of `app/`.

### Changed route

```
GET  /
    Adds an "N awaiting review" count near the top, from
    SELECT COUNT(*) FROM runs WHERE status = 'awaiting_review'.
```

Nothing about `/runs/{run_id}` changed - an orchestrator-created run reviews exactly like any
other; verified live at both L2 (edited value applied) and L4 (acknowledge + issue-token +
`commit_changes`) in slice 2, and the orchestrator reuses that same path unmodified.

---

## 10. Guardrails

Unchanged. The orchestrator only ever gets the tools `build_server(level)` hands it, so
`check_no_milestone_date_change` applies to it exactly as it does to Claude Code - this
slice's job is to prove that, not to add to it.

---

## 11. `[DONE]` Verification script

```
scripts/demo_slice3.py   runs the orchestrator directly against the real site and narrates
                          the same before/after story as demo_slice1.py/demo_slice2.py, then
                          pauses for approval in the browser exactly like they do.
                          Usage: uv run python scripts/demo_slice3.py <target_jql> [level] [instructions]
```

The difference from slice 1/2's demo scripts: this one's "staged proposals" section isn't
pre-seeded by a script standing in for the agent - it's whatever Gemini actually decided to
propose, for real, when it ran. Not yet run against the live site end-to-end (blocked on the
Gemini free-tier daily quota - see section 4) but exercises the same `run_reestimate_task`
already proven live via the debug harness during development.

## 12. `[DONE]` Tests

The LLM call itself is non-deterministic and costs money - it does not belong in the test
suite that runs on every `pytest` invocation. Four tests, all with Gemini stubbed/faked, all
in `tests/test_orchestrator.py` and `tests/test_agent_console.py`:

1. `test_orchestrator_drives_the_real_gateway_through_a_scripted_sequence` - a fake Gemini
   client scripts a fixed tool-call sequence (`search_issues` -> `start_run` ->
   `propose_estimate_change` -> `finish_run`, reading run_id back out of the growing
   conversation the way a real model would); `run_reestimate_task` drives a real in-process
   MCP gateway through it and returns the correct run_id - proves the *loop*, not the model.
   This test is what caught both bugs in section 8.
2. `test_orchestrator_at_l1_fails_cleanly_with_no_write_tools` - at L1, a fake model that
   never calls a tool (nothing it can do with read-only access) makes the orchestrator fail
   with `OrchestratorError`, and zero rows land in `runs`. Regression guard that L1's tool
   boundary holds for this caller too, not just for Claude Code.
3. `test_agent_console_run_creates_a_run_and_redirects` / `..._surfaces_orchestrator_failure_as_502`
   - `POST /agent-console/run` with a stubbed orchestrator creates a run and redirects to
   `/runs/{run_id}` on success, or returns 502 with the failure message on `OrchestratorError`.
4. `test_index_reports_awaiting_review_count` / `..._shows_no_badge_when_nothing_awaiting_review`
   - `GET /` reports the correct count, and shows nothing when it's zero.

All 29 tests pass (23 from slices 1-2 unmodified, 6 new here - two beyond the plan's four
tests, for the no-badge-at-zero case and the 502 path separately).

## 13. Definition of done

- [x] `runs.agent` distinguishes an orchestrator run (`orchestrator:gemini-3.6-flash`) from a
      Claude-Code-driven one (`planning`) - proven by `test_orchestrator_drives_the_real_gateway_...`.
- [x] Running the orchestrator at L1 fails cleanly (no `start_run` tool available to it) -
      `test_orchestrator_at_l1_fails_cleanly_with_no_write_tools`.
- [x] All tests from slices 1-2 still pass, plus six new ones here (LLM call stubbed) - 29 total.
- [x] The resulting run reviews and approves exactly like any other at whatever level it ran
      at - not re-tested this slice since nothing about `/runs/{run_id}` changed; slice 2
      already proved this live at L2/L3/L4.
- [x] **The form at `/agent-console` runs a real Gemini API call against the real gateway,
      through the actual web route.** Verified 2026-09-08 after billing was enabled: proposed
      MCP-10 story_points 1.0 -> 2.0 at confidence 0.85 with real reasoning about the task;
      approving in the browser changed Jira for real (confirmed `points=2.0` via
      `scripts/check_connection.py`-style read afterward).
- [x] `scripts/demo_slice3.py` run against the real site. All six steps printed correctly for
      a fresh live run (MCP-7, L3): current state (8.0) -> handed off to Gemini -> staged
      proposal (3.0, confidence 0.85, real reasoning about splitting notification workers) ->
      approved in the browser -> confirmed Jira updated to 3.0 -> audit trail showing
      `agent:orchestrator:gemini-3.6-flash` for the propose/finish steps.

Every item in this list is now checked. Slice 3 is complete.

---

## Starting prompt for Claude Code

```
Read CLAUDE.md, docs/slice-2.md's "Slice 3 preview" note, and docs/slice-3.md
before writing anything. Sections 3, 4, 5, and 7 are already resolved/built -
read them for context, don't redo them.

Build the rest of slice 3 in this order, pausing after each step for review:

1. orchestrator/ - agent.py (MCP client + Gemini tool-use loop) and
   prompts.py. Test it against the real gateway and real Jira by hand
   before wiring up the web route.
2. app/ - GET /agent-console, POST /agent-console/run, the awaiting_review
   count on GET /.
3. scripts/demo_slice3.py against the real site.
4. tests/ - the four tests in docs/slice-3.md section 12, Gemini call
   stubbed.

Rules: same as slices 1-2 - verify the orchestrator against the real
gateway and real Jira, never a mock of the gateway; nothing in gateway/
prints to stdout; explain each file in 2-3 sentences; stop and ask if
anything is ambiguous rather than guessing.
```

---

## Addendum: the token watcher (2026-09-08, post-slice)

Added after the slice was otherwise complete, on request - not planned in section 2, which
explicitly ruled out a background job queue for this slice. Worth calling out honestly rather
than silently folding into the plan above.

**The problem:** at L4, issuing a token in the browser doesn't apply anything - only an agent
calling `commit_changes` with it does. Without an orchestrator watching for that, every token
needed a human to paste it into a Claude Code chat by hand, or ask this assistant to spend it
directly. Tested, but manual every time.

**What was asked, and rejected:** making "Issue token" apply directly, collapsing L4 into
L3's direct-apply. Refused - it would erase the one thing L4 is for (two distinct actors: a
human who can authorise, an agent who can act, neither sufficient alone).

**What was built instead:** `orchestrator/token_watcher.py` - a small, standalone,
LLM-free polling loop (`uv run python -m orchestrator.token_watcher [poll_seconds]`,
default 10s). `find_due_tokens()` is the only real logic: unconsumed, unexpired tokens on
`L4` runs still `awaiting_review`. For each one found, `commit_with_token()` spends it via a
**real MCP client call** - a fresh gateway subprocess at L4, same as any other agent caller,
not a backdoor into `GatewayTools`. No model call anywhere in this path: spending an
already-authorised token is mechanical, not a judgement call, so there's nothing for an LLM
to reason about at this step.

Runs with its own agent identity, `orchestrator:token-watcher` - distinct from
`orchestrator:gemini-3.6-flash` (which proposed the change) and `human:reviewer` (which
issued the token), so the audit trail stays honest about which of three different actors did
what, rather than blurring "the LLM that reasoned about story points" with "the process that
mechanically noticed a token existed."

It's a separate process, not started by anything else - a human runs it the same way they'd
choose to keep an interactive Claude Code session open. `tests/test_token_watcher.py` covers
`find_due_tokens()` (6 cases: found, consumed, expired, wrong level, wrong status, multiple
runs at once); the actual commit path reuses the exact MCP call
`tests/test_orchestrator.py`/`tests/test_commit_changes.py` already prove.

**Verified live**: seeded an L4 run, started the watcher, acknowledged and issued a token in
the browser - the watcher picked it up within one 5s poll cycle and committed it without any
manual step. Confirmed via Jira (`points=2.0`) and the audit trail:
`agent:planning` proposed -> `human:reviewer` issued the token -> `agent:orchestrator:token-watcher`
committed, three seconds later, unprompted.
