# Slice 4 — assumptions, renaming, and the L1 chat surface

**Built and verified live** — real Jira site, real Gemini call, both new UI surfaces exercised
in the browser (not just screenshotted). 48 tests passing (40 pre-existing plus 8 new:
`tests/test_chat.py`'s 3, `tests/test_chat_routes.py`'s 4, and one net new
`propose_estimate_change` case replacing the old confidence-range test with two - empty and
`"NA"`). Three independent changes bundled into one slice because each is small on its own;
see sections 1-3. Verify live with `scripts/demo_slice4.py`.

**Goal:** act on early feedback gathered outside the codebase (a list of usability/roadmap
notes, not a formal review) without touching the oversight architecture itself. Two of the
three items here are UI/data-shape changes; the third (L1 chat) is the first thing built
against `L1` since it became a level slice 1 defined but never gave a UI entry point.

---

## 1. Confidence → assumptions

**Problem:** `propose_estimate_change`/`propose_due_date_change` required a `confidence`
float (0.0-1.0), rendered as a percentage pill on the review screen. Feedback: a bare
percentage implies a precision the model doesn't actually have, and gives the reviewer
nothing to act on beyond "trust this number or don't."

**Change:** replaced the `confidence` column and parameter with `assumptions` — free text.
The agent states what it assumed (an unclear acceptance criterion, an unknown dependency,
scope guessed at) so a PM has something concrete to evaluate; if it made no real assumption,
it passes the literal string `"NA"`. No numeric validation remains — `_validate_proposal`
now only checks `assumptions` is non-empty and `reasoning` is at least 20 characters, as
before.

Touched: `db/migrations/0003_replace_confidence_with_assumptions.sql` (SQLite
rename/recreate/copy — `ALTER TABLE` can't change a column's type or drop `NOT NULL` in
place), `gateway/server.py` (tool signatures, `_validate_proposal`, `finish_run`'s summary
dict), `orchestrator/prompts.py` (system prompt instruction), `app/templates/run.html` (the
Assumptions column replaces the Confidence pill), `app/static/style.css`
(`.confidence-pill` removed, `.assumptions-cell` added), and every test/demo script that
constructed a `confidence=` kwarg or printed one.

---

## 2. L3 rename: Drafter → Analyst

**Problem:** feedback that "Drafter" reads as a documentation-writing agent, not a PM agent
that prepares a locked, finished proposal for review.

**Change:** `app/levels.py`'s `L3` product name is now **Analyst**. Presentation-only, same
as the 2026-09-10 renaming — `TOOLS_BY_LEVEL`, `AGENTIC_PM_LEVEL`, `runs.level`, and every
test still use `"L3"` exactly as before. Also fixed two places that had hardcoded the old
name in template text instead of calling `level_name("L3")` (`app/templates/run.html`'s
mode-note, `app/templates/agent_console.html`'s mode picker) — same class of bug that made
this rename need a grep in the first place, worth not repeating next time.

---

## 3. L1 (Advisor) chat

**Problem:** `L1` has always existed in `TOOLS_BY_LEVEL` (read-only tools) but the Agent
Console never offered it as a working mode — it can't drive the `reestimate` task type at
all, since that task requires `start_run`, which L1 doesn't have. Until this slice, L1 was
unreachable through the UI. Separately, feedback asked for "conversational functionality for
level 1" specifically.

**Tension with CLAUDE.md:** section 1 originally said "there is no chat panel anywhere in
this product," full stop. Explicitly overridden for this one case — see CLAUDE.md's
2026-09-15 addendum for the reasoning, not repeated here.

**Design:** `orchestrator/chat.py`'s `run_chat_turn()` spawns the gateway the same way
`orchestrator/agent.py` does, except pinned to `AGENTIC_PM_LEVEL=L1` and with a system prompt
for Q&A instead of task execution. This is the same enforcement mechanism as every other
level in this system, just interactive instead of one-shot: the chat agent structurally only
ever receives `search_issues`/`get_issue`, so it cannot stage or apply a change no matter how
it's prompted — there is no tool for that to call. `app/main.py` adds `GET`/`POST /chat`,
backed by two new tables (`db/migrations/0004_add_chat.sql`: `chat_sessions`,
`chat_messages`) holding one ongoing conversation — no login, no session picker, the same
"exactly one implicit reviewer" simplification slice 1 made for approvals. Each turn is
audit-logged (`action="chat_turn"`, `level="L1"`, `run_id=NULL` since a chat turn never opens
a run) so it has the same provenance trail as everything else in the system.

`orchestrator/mcp_bridge.py` is new: pulled the MCP↔Gemini plumbing (tool-schema conversion,
`structured_content` unwrapping, function-response wrapping) out of `orchestrator/agent.py`
so `chat.py` doesn't duplicate it. Behaviour of `run_reestimate_task` is unchanged; this is a
pure extraction, covered by the pre-existing `tests/test_orchestrator.py` suite still passing
unmodified.

**Not built:** persistent multi-session chat (multiple named conversations), streaming
partial responses, or letting the chat surface drive any level above L1 — all out of scope
for what was actually requested.

---

## 4. Definition of done

- [x] Runs against the real Jira site — `scripts/demo_slice4.py`.
- [x] Demo script prints before/after: part 1 shows the assumptions field on a real staged
      proposal; part 2 reads Jira before and after a live chat turn and shows the state is
      identical (the read-only refusal path, made concrete rather than an explicit test of a
      guardrail that doesn't apply here — there's nothing to refuse, structurally).
- [x] At least one refusal-shaped test: `test_propose_estimate_change_rejects_empty_assumptions`,
      and `test_chat_turn_raises_when_the_model_never_gives_a_final_answer` (the chat
      equivalent of "the thing the system will not do" — answer a question it can't actually
      answer from read tools alone).
- [x] `claude mcp list` / `/mcp` still show the same tool set per level — this slice added no
      new gateway tools, only changed two tool's parameter shape.
- [x] Every file explainable without re-reading — see sections 1-3 above.
