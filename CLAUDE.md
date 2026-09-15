# Agentic PM — project context

Read this file at the start of every session. It records decisions that have already been
made. Do not relitigate them; if you think one is wrong, say so and wait.

---

## 1. What this project is

A **control plane for agentic project management, built on top of Jira.**

Jira owns the project data — issues, sprints, boards, estimates. This system owns the
**oversight layer**: how a human delegates work to an AI agent, at what level of autonomy,
and how they review what came back.

**This is a final-year research project (Monash FIT4701).** The contribution is the
oversight architecture, not the feature count. When in doubt, make the oversight mechanism
more rigorous rather than adding another agent capability.

### What we are NOT building

- A project management tool. No backlog CRUD, no board, no gantt. Jira does that.
- A general chat assistant. **Amended 2026-09-15**: the original line here was "there is no
  chat panel anywhere in this product," full stop. Slice 4 adds one narrow exception: an L1
  (Advisor) conversational Q&A surface at `/chat` (`orchestrator/chat.py`), explicitly
  requested to make L1 usable at all — until slice 4, L1 had no UI entry point whatsoever
  (see `app/levels.py`'s old comment). It is not a general assistant: it is spawned with
  `AGENTIC_PM_LEVEL=L1` exactly like every other orchestrator call, so it structurally only
  ever gets `search_issues`/`get_issue` — the same tool-gating mechanism from section 2
  applies to it, it just happens to be interactive instead of one-shot. It cannot stage or
  apply anything, cannot be reconfigured from the UI to a higher level, and there is still no
  chat surface at L2/L3/L4 or anywhere else in the product.
- Anything that talks to a real email server, calendar, or meeting recorder.

---

## 2. The core architectural decision

> **Autonomy is enforced by the tool layer, not by the prompt.**

The level a task runs at determines **which MCP tools exist for that run**. We never ask a
model to please behave at L2. We hand it a tool set that makes anything above L2 impossible.

This is the whole point of the project. If you ever find yourself writing "the agent should
ask for permission before…" into a system prompt, stop — that belongs in the gateway.

### The four levels

| Level | Name | Tools the agent gets | Where its output lands |
|---|---|---|---|
| **L1** | AI-Assisted (Operator) | read-only tools only | a suggestion tray; human retypes it |
| **L2** | Human-AI Collaborative | read + `propose_*` | staging table, human edits, human commits |
| **L3** | Supervised-AI (Consultant) | read + `propose_*` | staging table, **locked** — human approves / comments / re-runs, cannot edit |
| **L4** | Guided AI-Autonomy (Approver) | read + `propose_*` + `commit_changes` | staging table; commit requires a batch token issued only after per-item acknowledgement |

Notes that matter:

- **L1 does not register write tools at all.** Not disabled — absent from the tool list.
- **L2 vs L3 is enforced in the UI, not the tool layer** — same tools, but the L3 review
  surface renders staged changes read-only. Say this honestly in code comments; it is a real
  design seam and the report discusses it.
- **L4 is the only level where the agent can call `commit_changes`**, and it still cannot
  succeed without a token the human's browser issued.
- **L5 (no human in the loop) does not exist in this system, deliberately.**

**Product-facing names (2026-09-10) diverge from the table above, on purpose.** The names in
this table are Assalaarachchi et al.'s academic framework (arXiv:2601.16392) — keep using
them here, in the report, and in the viva; they're the citable grounding and the mapping is
almost exact (their own worked example for "Human-AI Collaborative" is effort estimation,
this project's own task). But asked directly, a naive user found `L2`/`L3`/`L4` plus jargon
subtitles unusable in the actual UI. `app/levels.py` now maps each code to a separate,
friendlier product name shown in the app itself: `L2` → **Co-Pilot**, `L3` → **Analyst**,
`L4` → **Autopilot** (`L1` → **Advisor**). This is presentation-only — `TOOLS_BY_LEVEL`,
`AGENTIC_PM_LEVEL`, `runs.level`, and every test still use `"L1"`.."L4"` exactly as before.

**Renamed 2026-09-15**: `L3` was originally **Drafter**; changed to **Analyst** because
"Drafter" read as a documentation-writing agent, not a PM agent that prepares a finished,
locked proposal for review. Same day, `L1`/**Advisor** stopped being unreachable through the
UI — see the chat-panel amendment above.

### Guardrails sit above the levels

Guardrails are server-side validators in the gateway. They apply identically at L1 and L4.
An agent never negotiates with a guardrail — the call is refused and the refusal is logged.

Slice 1 implements one: **no change to an issue's due date if the issue is a milestone.**
More come later. Never implement a guardrail as a prompt instruction.

---

## 3. Repository layout

```
gateway/          MCP server. Jira REST client, level-scoped tool registration, guardrails.
app/              Control plane: FastAPI backend + server-rendered HTML. No SPA framework.
db/               SQLite schema and migrations. Staging, provenance, audit, approval tokens.
scripts/          Verification and seeding scripts run against the real Jira site.
docs/             Slice specs. Read the current one before building.
tests/            pytest. Guardrail refusals and token gating are the tests that matter.
.env              Secrets. Never committed. See SETUP.md.
.mcp.json         MCP server registration for Claude Code. Committed; contains no secrets.
```

---

## 4. Technology

- **Python 3.11+**, `uv` for dependency management.
- **MCP**: the official Python SDK, **stdio transport**.
- **Jira**: REST API v3 with Basic auth (email + API token). No OAuth, no Atlassian Rovo,
  no Forge app.
- **Web**: FastAPI + Jinja2 templates + plain CSS. No React, no build step, no Tailwind CDN.
- **DB**: SQLite. It is a research prototype; Postgres would be ceremony.
- **Tests**: pytest.

Do not add a dependency without saying why in the same message.

---

## 5. How to work in this repo

1. **One slice at a time.** Read `docs/slice-N.md` and build exactly that. If something
   outside the slice looks broken or missing, mention it, do not fix it.
2. **Verify against real Jira, never mocks.** Every slice ends with a script in `scripts/`
   that hits the live site and prints before/after state. "It should work" is not done.
3. **Never print to stdout in `gateway/`.** stdio is the MCP transport; a stray `print()`
   corrupts the protocol and the server silently fails to connect. Log to stderr or a file.
4. **Secrets stay in `.env`.** Never hardcode a token, never echo one into a log or a commit
   message, never write one into `.mcp.json`.
5. **Explain as you go.** After each meaningful file, give a 2–3 sentence plain-English
   summary of what it does. The author has to defend this code in a viva.
6. **Commit in small steps** with messages that say why, not what.
7. If a request is ambiguous, ask one question rather than guessing across three files.

---

## 6. Jira specifics that will bite you

- **Story points are a custom field and the ID varies by site.** Never hardcode
  `customfield_10016`. Discover it once via `GET /rest/api/3/field`, filter for a name
  containing "Story", and cache the ID in `.env` as `JIRA_STORY_POINTS_FIELD`.
- **Sprints are also a custom field** and live behind the Agile API
  (`/rest/agile/1.0/...`), not the platform API.
- **The JQL search endpoint has changed.** Check which of `GET /rest/api/3/search` and
  `POST /rest/api/3/search/jql` the site accepts before building on one; prefer the
  non-deprecated one and note the choice in a comment.
- **Descriptions and comments use ADF** (Atlassian Document Format), not plain text or
  markdown. Write a small helper for plain-text → ADF rather than hand-building it each time.
- **Provenance cannot live in Jira.** Jira's history will attribute every change to the API
  user. Provenance — which agent, at what level, approved by whom — lives in our own DB, and
  is optionally mirrored into a structured Jira comment. This is a deliberate decision.

---

## 7. Definition of done for any slice

- It runs against the real Jira site.
- There is a script in `scripts/` that demonstrates it, printing before and after.
- There is at least one test proving the **refusal** path — the thing the system will not do.
- `claude mcp list` shows the gateway connected, and `/mcp` lists the expected tools for the
  level under test.
- The author can explain every file without reading it again.
