# Orbit — project context

Read this file at the start of every session. It records decisions that have already been
made, stated as they stand today — not a changelog. Do not relitigate them; if you think one
is wrong, say so and wait.

---

## 1. What this project is

A **control plane for agentic project management, built on top of Jira.**

Jira owns the project data — issues, sprints, boards, estimates. This system owns the
**oversight layer**: how a human delegates work to an AI agent, at what level of autonomy,
and how they review what came back.

**This is a final-year research project (Monash FIT4701-3926).** The contribution is the
oversight architecture, not the feature count. When in doubt, make the oversight mechanism
more rigorous rather than adding another agent capability.

### What we are NOT building

- A project management tool. No backlog CRUD, no board, no gantt. Jira does that.
- A general chat assistant — with one narrow, deliberate exception. L1 (Consultant) has a
  conversational Q&A surface (`orchestrator/chat.py`, `/chat`), because L1 otherwise has no
  UI entry point at all — it has no write tools, so it cannot drive the reestimate task type
  the rest of the app is built around. It is spawned with `AGENTIC_PM_LEVEL=L1` exactly like
  every other orchestrator call, so it structurally only ever gets `search_issues`/`get_issue`
  — the same tool-gating mechanism from Section 2 applies to it, it's just interactive instead
  of one-shot. No chat surface exists at L2/L3/L4 or anywhere else in the product.
- Anything that talks to a real email server, calendar, or meeting recorder.

---

## 2. The core architectural decision

> **Autonomy is enforced by the tool layer, not by the prompt.**

The level a task runs at determines **which MCP tools exist for that run**. We never ask a
model to please behave at L2. We hand it a tool set that makes anything above L2 impossible.

This is the whole point of the project. If you ever find yourself writing "the agent should
ask for permission before…" into a system prompt, stop — that belongs in the gateway.

### The four levels

| Level | Academic name | Product name | Tools the agent gets | Where its output lands |
|---|---|---|---|---|
| **L1** | AI-Assisted (Operator) | Consultant | read-only tools only | a suggestion tray; human retypes it, or the L1 chat surface |
| **L2** | Human-AI Collaborative | Co-worker | read + `propose_*` | staging table, human edits, human commits |
| **L3** | Supervised-AI (Consultant) | Committer | read + `propose_*` | staging table, **locked** — human approves / comments / re-runs, cannot edit |
| **L4** | Guided AI-Autonomy (Approver) | Super-Pilot | read + `propose_*` + `commit_changes` | staging table; commit requires a batch token issued only after per-item acknowledgement |

Notes that matter:

- **L1 does not register write tools at all.** Not disabled — absent from the tool list.
- **L2 vs L3 is enforced in the UI, not the tool layer** — same tools, but the L3 review
  surface renders staged changes read-only. Say this honestly in code comments; it is a real
  design seam and the report discusses it.
- **L4 is the only level where the agent can call `commit_changes`**, and it still cannot
  succeed without a token the human's browser issued.
- **L5 (no human in the loop) does not exist in this system, deliberately.**

**Academic names vs. product names.** The "academic name" column is Assalaarachchi et al.'s
framework (arXiv:2601.16392) — use `L1`–`L4` and these names in the report and the viva;
they're the citable grounding, and the mapping is almost exact (their own worked example for
"Human-AI Collaborative" is effort estimation, this project's own task). The "product name"
column (`app/levels.py`, `LEVEL_DISPLAY`) is what the UI actually shows a naive user, because
raw level codes plus jargon subtitles tested as unusable. This is presentation-only:
`TOOLS_BY_LEVEL`, `AGENTIC_PM_LEVEL`, `runs.level`, and every test still use `"L1".."L4"`
exactly. (Product names have been renamed twice: `L3` was "Drafter", then "Analyst"; the current set —
Consultant, Co-worker, Committer, Super-Pilot, each with an "AI … You …" tagline — arrived with the
rebrand to **Orbit**. Note "Consultant" is also the academic name for L3 above — in the report,
always pair the L-code with the academic name.)

### Guardrails sit above the levels

Guardrails are server-side validators in the gateway (`gateway/guardrails.py`). They apply
identically at L1 and L4. An agent never negotiates with a guardrail — the call is refused
and the refusal is logged.

One is implemented: **no change to an issue's due date if the issue is a milestone.** More
come later. Never implement a guardrail as a prompt instruction.

---

## 3. What's built — the five surfaces

Every page lives under one FastAPI app (`app/main.py`) and shares one app shell taken from the v7
Figma prototype (`FIT4701-Agentic-PM-Prototype-v7.fig`): a left sidebar (project card, nav grouped
under AGENTS and CONFIGURATION, an Approvals badge counting runs waiting on a human, a Jira site card)
and a top bar (breadcrumb, page title, Open in Jira). `/` redirects to the Dashboard, the prototype's
first nav item. The pages:

- **New task** (`/agent-console`; the Agent console) — a 3-step wizard: pick a task type (only Effort
  Estimation is real; the rest are visible-but-disabled "soon" chips, each already carrying a
  fixed recommended-autonomy mapping so the mapping is correct the moment it ships) → pick a
  working mode (a "Recommended" badge steers higher-risk task types toward lower autonomy) →
  either a Run confirmation for L2–L4 (submits to the orchestrator, redirects to the run's
  review page) or, for L1, the chat panel embedded directly as step 3 — a read-only level has
  no proposal to review, so the conversation itself is both the run and the review.
- **Chat** (`/chat`, its own sidebar tab) — the same L1 conversation as the console's embedded step, one shared
  DB-backed session (`_chat_thread.html` is the shared macro), reachable directly.
- **Approvals** (`/approvals`, `/runs/{id}`) — runs waiting on a human first (oldest first), then every earlier run, and per run the staged-changes review surface:
  editable at L2, locked at L3, acknowledge-then-issue-token at L4.
- **Dashboard** (`/dashboard`) — read-only project analytics: stat cards, a status-breakdown
  bar chart, an estimation-coverage donut, a "story points remaining over time" chart
  (explicitly not a sprint burndown — this prototype has no sprint start/end dates), a
  pace-based forecast, and fixed rule-based suggestions (deliberately not LLM-driven — free,
  deterministic, testable).
- **Activity** (`/audit`; the audit log) — the full provenance trail, with All / Changed Jira / Needs you / Rejected / Advice only filters, grouped per run into a native
  `<details>` disclosure (zero JS) with humanised actor/action labels — see
  `app/audit_display.py` for why `agent:planning` displays as "Human (via Claude Code)", not
  as an AI.
- **Settings** (`/settings`) — read-only: what each level may do, generated from `TOOLS_BY_LEVEL`
  (so it cannot drift from what the gateway enforces), the guardrail, and the recommended level per task
  type. No editable settings, no Memory page: the prototype's Memory, undo window and approval expiry
  are not built.

All share one design system (`app/static/style.css`, tokens from the Figma file: Geologica on a 4px type grid (12/16/20/24/32/40), #f8f8f8 ground,
16px white cards, charcoal primary buttons, one accent colour per autonomy level): CSS custom properties for colour
(reused directly as SVG `fill`/`stroke` in `app/charts.py`, so a chart can never drift from a
pill's colour), a consistent stat-card/chip/level-card vocabulary, and an "agents can make
mistakes" disclaimer next to any AI-generated content, plus a persistent one in the footer.

---

## 4. Repository layout

```
gateway/            MCP server: Jira REST client, level-scoped tool registration, guardrails.
  jira.py              JiraClient (REST v3) + search_issues_for_analytics() — a second, dashboard-
                        only read method with extra fields; deliberately kept separate from
                        search_issues()/Issue, which is a fixed contract the MCP tool payloads
                        (and their tests) depend on. Don't merge them.
  server.py            GatewayTools, TOOLS_BY_LEVEL, build_server(), main().
  guardrails.py        check_no_milestone_date_change().
  apply.py             apply_staged_changes() — shared by the L3 approve route and the L4
                        commit_changes tool, so both write paths run identical logic.

orchestrator/        Callers of the gateway — human-via-Claude-Code and LLM alike hit the same
                      MCP tool surface for a given level.
  agent.py             run_reestimate_task() — the one-shot task orchestrator (Gemini).
  chat.py              run_chat_turn() — the L1 conversational surface (read-only tools only).
  token_watcher.py     Automated L4 commit-handoff: polls for issued tokens, spends them. No LLM.
  mcp_bridge.py        Shared MCP<->Gemini plumbing (tool-schema conversion, result unwrapping).
  prompts.py           System/task prompts for the reestimate task.

app/                Control plane: FastAPI + Jinja2, server-rendered. No SPA framework.
  main.py              All routes.
  levels.py            LEVEL_DISPLAY (names, taglines, what each level creates/writes), TASK_TYPES (label +
                        recommended level, shared by New task and Settings), tool labels.
  run_display.py       Plain-language run fields: friendly_scope(), dates, ago(), humanize().
  analytics.py         build_dashboard_data() — pure aggregation/forecast/suggestions, no Jira call.
  charts.py            Inline-SVG chart builders (bar/donut/area) — no charting library, no build step.
  audit_display.py     friendly_action()/friendly_actor()/group_audit_rows() for the audit log.
  chat_markdown.py     Narrow markdown->HTML renderer for chat replies (bold + bullets only).
  jobs.py              In-memory background jobs with live progress: runs an orchestrator/chat loop after the
                        request returns, records the real tool calls it makes as plain-language steps; the page
                        polls GET /jobs/{id} (static/progress.js draws them). No queue, no DB - a one-user prototype.
  templates/           base, index (Runs), agent_console (wizard), chat, dashboard, audit, run,
                        _chat_thread (shared chat-history macro).
  static/style.css     One stylesheet, cache-busted by app.main._static_version() (the file's
                        own mtime) — a normal reload always gets current CSS.

db/                  SQLite schema, migrations, audit logging.
  migrate.py            apply_migrations(), get_connection().
  audit.py              log_audit() — append-only; nothing may UPDATE or DELETE a row.
  migrations/           0001 init, 0002 edited_value, 0003 confidence->assumptions,
                         0004 chat tables. Applied automatically on every connection.

scripts/             Verification scripts — every one hits the real Jira site, never a mock.
                      seed_demo.py, check_connection.py, demo_slice1.py .. demo_slice5.py.
docs/                Slice specs, read before building. Currently slice-1.md .. slice-5.md —
                      read the highest-numbered one for what's actually current.
tests/                pytest. Refusal paths and tool-gating are the tests that matter most —
                      run the whole suite (`uv run pytest`) before considering any change done.
.env                 Secrets. Never committed. See SETUP.md.
.mcp.json            MCP server registration for Claude Code. Committed; contains no secrets.
```

---

## 5. Technology

- **Python 3.11+**, `uv` for dependency management.
- **MCP**: the official Python SDK, **stdio transport**.
- **Jira**: REST API v3 with Basic auth (email + API token). No OAuth, no Atlassian Rovo,
  no Forge app.
- **Web**: FastAPI + Jinja2 templates + plain CSS. No React, no build step, no Tailwind CDN.
  Where a page needs interactivity (the console wizard, copy-to-clipboard, the L1 redirect),
  it's a small inline vanilla `<script>` — the same minimal pattern every time, not a framework.
- **LLM**: Google Gemini via the `google-genai` SDK, driven through a real MCP client — never
  a Python backdoor into `GatewayTools`.
- **DB**: SQLite. It is a research prototype; Postgres would be ceremony.
- **Tests**: pytest.

Do not add a dependency without saying why in the same message.

---

## 6. How to work in this repo

1. **One slice at a time.** Read the current `docs/slice-N.md` and build exactly that. If
   something outside the slice looks broken or missing, mention it, do not fix it.
2. **Verify against real Jira, never mocks.** Every slice ends with a script in `scripts/`
   that hits the live site and prints before/after state. "It should work" is not done.
3. **Never print to stdout in `gateway/`.** stdio is the MCP transport; a stray `print()`
   corrupts the protocol and the server silently fails to connect. Log to stderr or a file.
4. **Secrets stay in `.env`.** Never hardcode a token, never echo one into a log or a commit
   message, never write one into `.mcp.json`.
5. **Explain as you go.** After each meaningful file, give a 2–3 sentence plain-English
   summary of what it does. The author has to defend this code in a viva.
6. **Commit in small steps** with messages that say why, not what. The user commits; give
   them the message text, do not run `git commit` yourself.
7. If a request is ambiguous, ask one question rather than guessing across three files.

---

## 7. Jira specifics that will bite you

- **Story points are a custom field and the ID varies by site.** Never hardcode
  `customfield_10016`. Discover it once via `GET /rest/api/3/field`, filter for a name
  containing "Story", and cache the ID in `.env` as `JIRA_STORY_POINTS_FIELD`.
- **Sprints are also a custom field** and live behind the Agile API
  (`/rest/agile/1.0/...`), not the platform API — this project doesn't use it, which is why
  the dashboard's "points remaining over time" chart is explicitly not a sprint burndown.
- **The JQL search endpoint has changed.** Check which of `GET /rest/api/3/search` and
  `POST /rest/api/3/search/jql` the site accepts before building on one; prefer the
  non-deprecated one and note the choice in a comment.
- **Descriptions and comments use ADF** (Atlassian Document Format), not plain text or
  markdown. Write a small helper for plain-text → ADF rather than hand-building it each time.
- **Provenance cannot live in Jira.** Jira's history will attribute every change to the API
  user. Provenance — which agent, at what level, approved by whom — lives in our own DB
  (`audit_log`, and the friendlier rendering in `app/audit_display.py`), and is optionally
  mirrored into a structured Jira comment. This is a deliberate decision.

---

## 8. Definition of done for any slice

- It runs against the real Jira site.
- There is a script in `scripts/` that demonstrates it, printing before and after.
- There is at least one test proving the **refusal** path — the thing the system will not do.
- `claude mcp list` shows the gateway connected, and `/mcp` lists the expected tools for the
  level under test.
- The author can explain every file without reading it again.
