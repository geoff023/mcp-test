# Slice 2 — levels, for real

**Built and shipped.** Both `[DECIDE]` items below were resolved as recommended (L1: no new
code; L4 token: displayed in the UI, human relays it) and all four tests in section 10 pass
alongside slice 1's original 18. See the commit history for `db/migrations/0002_*`,
`gateway/apply.py`, `app/main.py`'s `/issue-token`/`/acknowledge` routes, and
`scripts/demo_slice2.py` for what actually landed.

**Goal:** turn the four-row table in CLAUDE.md section 2 into something demonstrable, not
just implemented. Slice 1 proved the spine at L3. This slice proves the other three actually
differ from each other — and from L3 — in ways a reader can see, not just read about.

**Estimated size:** 200–350 lines. Smaller than slice 1 on purpose: most of the enforcement
mechanism already exists.

---

## 0. What already works, unmodified

This is worth stating plainly because it's the payoff of slice 1's design:

- `gateway/server.py`'s `TOOLS_BY_LEVEL` **already** registers L1 as read-only and L4 with
  `commit_changes`. `tests/test_server_tool_registration.py` already proves it. **No gateway
  changes are needed for tool registration.**
- `gateway/guardrails.py` already runs identically at every level — nothing to do there.
- `db/schema.sql`'s `staged_changes.acknowledged` column already exists, unused since slice 1
  had no level that needed it. L4 is the first consumer.

What's missing is entirely in `app/` (the review surface has to *look different* per level)
and in one new write path (L4 issuing a token without applying anything itself).

---

## 1. In scope

- **L1**: confirm the read-only tool set is sufficient; decide whether anything needs to
  exist in `app/` for it at all (see the `[DECIDE]` below — probably not).
- **L2**: the review surface renders staged changes with editable fields. Approving applies
  the human's edited values, not necessarily the agent's original proposal, and the audit
  trail says so.
- **L4**: the review surface renders read-only (like L3) but adds a per-row acknowledge
  checkbox. Issuing an approval token is disabled until every staged change on the run is
  acknowledged. Issuing a token does **not** apply anything — that only happens when the
  agent calls `commit_changes` with it, same as slice 1's tests already exercise.

## 2. Explicitly out of scope

Still no orchestrator (Claude Code is still "the agent"), still no login, still no second
task type, still no sprint manipulation. No new guardrail — this slice is about the review
surface and the L4 handoff, not about writing more validators.

---

## 3. `[DECIDE]` — what does L1 actually need?

CLAUDE.md's table says L1's output "lands" in "a suggestion tray; human retypes it." Two
readings:

- **(A) Nothing new gets built.** L1 has no `start_run`/`propose_*` tools, so there is
  structurally no way for an L1 interaction to reach our DB. The "suggestion tray" is just
  Claude Code's own chat response — the agent reads issues via `search_issues`/`get_issue`
  and says "I'd suggest 3 points for MCP-4 because X," and the human manually types that into
  Jira. Zero lines of code. This is consistent with "no chat panel in this product" — the
  suggestion never enters *this* product at all.
- **(B) Add a narrow, non-staging `suggest_change` tool**, registered at L1 only, that logs
  the suggestion to a new lightweight table purely for display (never touches
  `staged_changes`, never touches Jira) so it shows up in a "Suggestions" view in the app.

**Recommendation: (A).** It costs nothing, it's arguably a more honest demonstration of "L1
has no write surface at all, not even a soft one," and (B) adds a table, a tool, and a route
for a level whose entire point is *not having a footprint here*. Tell me if you want (B)
instead — the report can defend either reading, but they're different amounts of work.

---

## 4. `[DECIDE]` — how does a token reach the agent at L4?

There is still no orchestrator process the app can call into. So when a human clicks "Issue
token" for an L4 run, the token has to reach the agent somehow so it can call
`commit_changes(run_id, token)`.

- **(A) Display it in the UI**, human copies it, pastes it into their Claude Code chat
  ("here's the token, go commit run X"). Manual, a bit clunky, but honest about the current
  architecture and needs nothing new beyond a text box.
- **(B) Nothing extra** — same as (A) but framed as expected: slice 2's job is to prove the
  *gate* (no token without full acknowledgement), not to build a notification channel.

These are really the same option described two ways. **Recommendation: (A)/(B)** — display
the token, plain text, with a copy button if you want minor polish. No queue, no websocket,
no polling. A real handoff channel is orchestrator territory, later.

---

## 5. DB schema changes

One additive column, one new small table's worth of nothing (no new table needed):

```sql
ALTER TABLE staged_changes ADD COLUMN edited_value TEXT;
```

`edited_value` is NULL unless a human changes it at L2 review time. `new_value` stays exactly
what the agent proposed — provenance is "what did the agent say, what did the human actually
do," not one column doing both jobs. `apply_staged_changes` (gateway/apply.py) applies
`COALESCE(edited_value, new_value)`.

No migration numbering scheme exists yet (schema.sql is applied as one script, tracked by
filename in `schema_migrations`) — this slice is the first time schema.sql actually changes
after being applied to a real `.db` file. `db/migrate.py`'s current logic (skip if
`schema.sql` was already applied, by filename) **will not pick up this change** on an
existing database. Options: bump to a real migrations directory (`db/migrations/0002_*.sql`)
now, or just delete the local `db/agentic_pm.db` and re-migrate (fine for a solo research
prototype, not fine as a pattern going forward). **Recommend switching to numbered migration
files in this slice** — it's the first real test of whether `migrate.py`'s design holds up,
and CLAUDE.md's DB layout comment already anticipated migrations plural.

---

## 6. MCP tool contract

**Unchanged.** Confirmed by re-reading `gateway/server.py`: `TOOLS_BY_LEVEL` already does the
right thing for all four levels. This section exists only to say so explicitly, so nobody
goes looking for gateway work that isn't there.

---

## 7. The web app

### New route

```
POST /runs/{run_id}/changes/{change_id}/acknowledge
    L4 only. Toggles staged_changes.acknowledged for one row. Redirects back to the review page.

POST /runs/{run_id}/issue-token
    L4 only. Refuses (400) unless every staged change on the run has acknowledged = 1.
    Creates an approval_tokens row (NOT consumed), renders it back to the human to relay
    to the agent. Does not touch Jira, does not mark the run applied - that only happens
    when commit_changes consumes the token.
```

### Changed route

```
GET /runs/{run_id}
    Branches on run["level"]:
      L2 -> editable <input> per staged-changes row (bound to edited_value), single
            "Approve" submit carries all edits in the POST body.
      L3 -> unchanged from slice 1 (read-only table, Approve / Send back).
      L4 -> read-only table (like L3) + one checkbox per row (acknowledge) + a disabled/
            enabled "Issue token" button depending on whether all rows are acknowledged,
            instead of an "Approve" button. No direct-apply path at L4 - only the app
            issuing a token, and the agent consuming it via commit_changes.

POST /runs/{run_id}/approve
    At L2: applies edited_value (falling back to new_value) instead of new_value outright.
    Audit detail records which issues were edited vs. taken as proposed.
    At L3: unchanged.
    Not registered/reachable at L4 - that level uses /issue-token instead.
```

`app/main.py`'s `apply_staged_changes` call already lives in one shared function
(`gateway/apply.py`) — L2's edited-value support is a one-line change there
(`new_value` → `COALESCE(edited_value, new_value)` in the SQL, or the equivalent in Python).

---

## 8. Guardrails

No changes. `check_no_milestone_date_change` already runs inside `apply_staged_changes`,
which both the L2/L3 approve route and the L4 `commit_changes` tool already share. This
slice's due-date story is identical to slice 1's — still demonstrated via
`propose_due_date_change`, still refused at every level, L4 included.

---

## 9. Verification scripts

```
scripts/demo_slice2.py   the L2 and L4 stories, back to back, against the real site
```

Two narrated flows, same shape as `demo_slice1.py`:

1. **L2 flow**: agent proposes 5 points for an issue; script shows the review page has an
   editable field; a human (you, at the keyboard) changes it to 3 in the browser and
   approves; script confirms Jira shows 3, not 5, and the audit row says the value was
   edited.
2. **L4 flow**: agent proposes a change; script shows the "Issue token" button is disabled
   with zero items acknowledged; you acknowledge the one item in the browser; script shows
   the button is now enabled; you click it; script reads the token from the DB (or you paste
   it back into the script — `[DECIDE]` above affects this); calls `commit_changes` directly
   (standing in for the agent, same as `demo_slice1.py` stands in for it) and confirms Jira
   updated.

## 10. Tests

Four more tests that matter:

1. At L2, approving applies `edited_value` when present, `new_value` when not; the audit
   detail distinguishes "applied as proposed" from "applied as edited."
2. `/runs/{id}/issue-token` refuses with 400 while any `staged_changes.acknowledged = 0` for
   that run, and does not create a token row when it refuses.
3. Once every row is acknowledged, `/issue-token` creates an unconsumed token; `commit_changes`
   then succeeds with it exactly as slice 1's `test_commit_changes.py` already proves for a
   valid token — this test is really "the L4 UI actually produces a token
   `commit_changes` accepts," not new logic in `commit_changes` itself.
4. L1 still has no `start_run` in its tool list (regression guard on slice 1's
   `test_server_tool_registration.py` — a one-line addition there, not a new file).

## 11. Definition of done

- `docs/slice-2.md`'s two `[DECIDE]` items are resolved (in this doc or by you telling me).
- `/mcp` at L1 still shows only `search_issues`/`get_issue` (regression check).
- At L2, editing a value in the browser and approving changes *that* value in Jira, not the
  agent's original proposal — visible in the Jira UI.
- At L4, "Issue token" is disabled until every staged change is acknowledged, and enabled the
  moment the last one is; the issued token successfully drives `commit_changes`.
- `scripts/demo_slice2.py` runs clean against the real site for both flows.
- All slice 1 tests still pass, plus the four new ones above.

---

## Starting prompt for Claude Code

Once the two `[DECIDE]` items above are resolved:

```
Read CLAUDE.md and docs/slice-2.md before writing anything.

Build slice 2 in this order, pausing after each step for review:

1. db/schema.sql - add staged_changes.edited_value, switch to numbered
   migration files, migrate the real db.
2. gateway/apply.py - apply edited_value over new_value when present.
3. app/ - the L2 editable review surface, the L4 acknowledge checkbox
   and /issue-token route, the changed /runs/{id}/approve behaviour at L2.
4. scripts/demo_slice2.py against the real site.
5. tests/ - the four tests in docs/slice-2.md section 10.

Rules: same as slice 1 - verify against real Jira, never mocks; nothing
in gateway/ prints to stdout; explain each file in 2-3 sentences; stop
and ask if anything is ambiguous rather than guessing.
```

---

## Slice 3 preview (do not build yet)

**A note, not a spec.** Captured 2026-09-08 from a UI reference the user shared (an "Agent
Console" mockup, image not in the repo — description below; drop the PNG into `docs/assets/`
if it should be preserved pixel-exact). Direction only. Read this alongside CLAUDE.md before
actually planning slice 3 for real.

### The shift this represents

Every run so far has been triggered by a human typing a prompt into an interactive Claude
Code session, which then calls the MCP tools by hand. That was deliberate — slice 1 said the
orchestrator would come "much later," specifically so the gateway could be proven without
building an agent loop first. The mockup asks for that later thing: a UI where a human
configures a task (type, instructions, target list) and something *other than a chat session*
runs the agent loop. That something is a real orchestrator, for the first time.

### What the mockup shows

A 4-step wizard ("Agent Console"), replacing the chat prompt with a structured trigger:

1. **Define the task** — pill buttons for task type (mockup shows: Weekly Status Report,
   Standup / Meeting Digest, Risk Scan / Report, Sprint Planning, Effort Estimation,
   Retrospective Synthesis, Email Draft, Reminder & Nudges), an optional free-text
   "Instructions" box, and a "Target list" dropdown.
2. **Working mode** — not detailed in the mockup, but the obvious mapping is choosing L1-L4.
   That's already first-class here (`AGENTIC_PM_LEVEL`, `TOOLS_BY_LEVEL`) — this step needs a
   front-end control, not new backend design.
3. **Run** — the agent executes.
4. **Review & Decide** — **this already exists.** It's `GET /runs/{run_id}`
   (`app/templates/run.html`), which already branches by level. Nothing new here except
   however the wizard hands off into it.

The top bar's "Approvals (4)" badge maps directly to
`COUNT(*) FROM runs WHERE status = 'awaiting_review'` — a small addition to `GET /`.

### What's actually new

Steps 1-3, and only because step 3 currently has no non-human driver. A real orchestrator:
takes `(task_type, instructions, target_list, level)`, calls an LLM equipped with the read
tools, and drives the same `start_run` / `propose_*` / `finish_run` sequence Claude Code has
been driving by hand since slice 1. Everything downstream — staging, guardrails, the review
surface, the level gating — is unchanged; only *what calls the propose tools* changes.

### `[DECIDE]` — which task type first?

The instruction was: one easy, demonstrable task type before generalising across the
mockup's eight.

- **Effort Estimation** (the existing `reestimate` task). **Recommended.** Already works end
  to end at all four levels, verified against real Jira. The only new work is the
  orchestrator itself — swap "Claude Code in a terminal" for "a scripted LLM call using the
  same MCP tools," triggered by the wizard instead of a chat prompt. Zero new Jira surface,
  zero new guardrail, zero scope risk.
- **Sprint Planning** (the user's example in conversation). Would need real sprint
  manipulation via the Agile API (`/rest/agile/1.0/...`) — explicitly out of scope through
  slice 1 and 2 ("no sprint manipulation"). Picking this reopens that scope decision *and*
  adds a second Jira API surface before the orchestrator itself is even proven. Not
  recommended as the first one, though it's a natural second.

Also worth flagging, not deciding: four of the mockup's eight task types conflict with
CLAUDE.md section 1's stated non-goals as written. "Standup / Meeting Digest" and
"Retrospective Synthesis" imply a meeting recorder or notes source — CLAUDE.md rules out
"anything that talks to a real email server, calendar, or meeting recorder." "Email Draft"
implies real email sending — same rule. "Risk Scan / Report" isn't ruled out by CLAUDE.md
directly, but slice 1 explicitly deferred "no risk register." None of this blocks starting
with Effort Estimation — it just means the mockup is a longer-term product vision, and most
of its task types aren't slice-3-shaped as written.

### Rough shape, if Effort Estimation is picked

- A new `orchestrator/` (or `agent/`) module: takes a run config, calls an LLM (provider/SDK
  undecided) equipped with `search_issues`/`get_issue` plus the ability to call
  `propose_estimate_change`, and drives `start_run`/`finish_run` itself.
- One or two new app routes backing wizard steps 1-2 (e.g. `POST /agent-console/run`) that
  take task_type/instructions/target_list/level, kick off the orchestrator, and redirect into
  the existing `/runs/{run_id}` for steps 3-4.
- `GET /` grows an `awaiting_review` count for the Approvals badge.
- Provenance gap to resolve: `actor` is currently a hardcoded `"agent:planning"` string
  (`gateway/server.py`). An orchestrator calling an LLM directly — not through Claude Code —
  needs a real answer for what goes in that column, and probably which model/prompt version,
  for the audit trail to stay meaningful once more than one thing can call itself "the agent."
