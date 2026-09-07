# Slice 1 — the vertical slice

**Goal:** prove the entire architecture end to end on the smallest possible task.

When this works you will have demonstrated: reading from Jira, an agent proposing a change,
that change being staged rather than applied, a human approving it, the write reaching Jira,
a guardrail refusing something, and the whole thing recorded. Everything after this slice is
repetition and breadth.

**Estimated size:** 400–600 lines. If it is heading past 900, something has been
over-engineered.

---

## In scope

One task type: **re-estimate a story's points**.
One level: **L3 (Supervised-AI)**.
One guardrail: **no due-date changes on milestone issues**.

## Explicitly out of scope

No other agent, no other task type, no L1/L2/L4 yet, no styling beyond legibility, no login,
no multi-project support, no sprint manipulation, no documents, no risk register.

L1/L2/L4 arrive in slice 2. Resist adding them early — the point of slice 1 is a working
spine, not a feature.

---

## Architecture for this slice

```
   Browser (control plane UI)
        │  approve / reject
        ▼
   FastAPI app  ──issues approval token──┐
        │                                 │
        │ reads staged changes            │
        ▼                                 │
   SQLite  ◄── staged_changes ────────────┤
        ▲                                 │
        │                                 ▼
   MCP gateway (stdio) ──────────► Jira Cloud REST API
        ▲
        │ tools scoped by level
   Agent (Claude Code, for now)
```

For slice 1 the "agent" is just Claude Code itself calling your MCP tools. A dedicated
orchestrator comes much later. This is deliberate — it lets you test the gateway without
building an agent loop first.

---

## Database schema

```sql
CREATE TABLE runs (
  id            TEXT PRIMARY KEY,      -- uuid
  level         TEXT NOT NULL,         -- 'L1' | 'L2' | 'L3' | 'L4'
  task_type     TEXT NOT NULL,         -- 'reestimate'
  agent         TEXT NOT NULL,         -- 'planning'
  scope         TEXT NOT NULL,         -- JQL or issue key list
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL          -- 'running' | 'awaiting_review' | 'applied' | 'rejected'
);

CREATE TABLE staged_changes (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  issue_key     TEXT NOT NULL,
  field         TEXT NOT NULL,         -- 'story_points'
  old_value     TEXT,
  new_value     TEXT NOT NULL,
  reasoning     TEXT NOT NULL,         -- why the agent proposed this
  confidence    REAL NOT NULL,         -- 0.0 – 1.0
  acknowledged  INTEGER NOT NULL DEFAULT 0,
  applied_at    TEXT
);

CREATE TABLE approval_tokens (
  token         TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  issued_at     TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  consumed_at   TEXT
);

CREATE TABLE audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  run_id        TEXT,
  actor         TEXT NOT NULL,         -- 'agent:planning' | 'human:selena' | 'gateway'
  level         TEXT,
  action        TEXT NOT NULL,
  outcome       TEXT NOT NULL,
  detail        TEXT
);
```

`audit_log` is append-only. Nothing in the codebase may update or delete a row in it.

---

## MCP tool contract

The gateway reads a level from the `AGENTIC_PM_LEVEL` environment variable at startup
(default `L3`) and **registers a different tool set accordingly**. In slice 1 only the L3
path needs to work, but write the registration so adding the other levels is a matter of
listing tool names, not restructuring.

### Always registered (read)

```
search_issues(jql: str, max_results: int = 50) -> list[Issue]
    Returns key, summary, status, current story points, issue type, due date.

get_issue(issue_key: str) -> Issue
    Full detail including description as plain text.
```

### Registered at L2, L3, L4

```
start_run(task_type: str, scope: str) -> run_id
    Opens a run. Every proposal must belong to one.

propose_estimate_change(
    run_id: str,
    issue_key: str,
    new_points: float,
    reasoning: str,
    confidence: float
) -> staged_change_id
    Writes to staged_changes. Does NOT touch Jira.
    Rejects confidence outside 0–1.
    Rejects reasoning shorter than 20 characters — an unexplained proposal is not a proposal.

finish_run(run_id: str) -> summary
    Marks the run 'awaiting_review' and returns a summary for the human.
```

### Registered at L4 only

```
commit_changes(run_id: str, approval_token: str) -> result
    Applies staged changes to Jira. Fails loudly if the token is missing, unknown,
    expired, already consumed, or belongs to a different run.
```

**At L3 `commit_changes` must not appear in the tool list at all.** Writing happens through
the web app when the human presses Approve. Verify this: start the server at L3 and confirm
via `/mcp` that only the read and propose tools are listed.

---

## The guardrail

```
GUARDRAIL: no_milestone_date_change
```

Any attempt to modify the due date of an issue whose issue type is `Epic`, or which carries
the label `milestone`, is refused — at every level, including L4.

Implement it as a validator function called by every write path, not as a check inside one
tool. In slice 1 there is no date-changing tool yet, so add
`propose_due_date_change` **solely so the refusal can be demonstrated**, and have it refuse
on milestone issues. This is the test that makes the architecture credible.

The refusal must: return a structured error naming the guardrail, write an `audit_log` row
with outcome `refused`, and never partially apply.

---

## The web app

Four routes. Server-rendered HTML. Plain CSS. No framework.

```
GET  /                     list of runs, newest first, with status
GET  /runs/{run_id}        the review surface
POST /runs/{run_id}/approve
POST /runs/{run_id}/reject
GET  /audit                the audit log, newest first
```

### The review surface at L3

This is the screen that carries the research idea, so build it carefully even though it is
plain.

For each staged change show: issue key and summary, old value → new value, the agent's
reasoning, and the confidence. Rendered **read-only** — no input fields anywhere on the page.
Two actions only: **Approve** and **Send back**, plus a comment box whose text is stored on
the run when sending back.

Put one line of copy on the page stating why it is read-only: *at L3 the agent owns the
method; you approve it, comment on it, or send it back.*

Approving issues an approval token, applies the staged changes to Jira, marks them applied,
and writes audit rows. Rejecting marks the run rejected, applies nothing, and also writes an
audit row.

---

## Verification scripts

Three scripts in `scripts/`, all hitting the real site.

```
scripts/check_connection.py   prints the authenticated user and the project — the smoke test
scripts/seed_demo.py          creates ~8 issues with points, one Epic labelled 'milestone'
scripts/demo_slice1.py        prints the full before/after story
```

`demo_slice1.py` must print, in order:

1. Current story points for the target issues, read from Jira.
2. The staged proposals from the DB, with reasoning and confidence.
3. A pause telling the operator to approve in the browser.
4. The story points read from Jira **again**, showing the change landed.
5. The audit log rows for the run.

That output is your demo and your evidence. Make it readable.

---

## Tests

`tests/` with pytest. Four tests matter more than coverage:

1. `propose_estimate_change` writes to the staging table and makes **no** Jira call.
   (Assert on the HTTP client, do not just trust it.)
2. `commit_changes` fails with a missing, unknown, expired, consumed, or mismatched token.
3. The milestone guardrail refuses a due-date change on an Epic labelled `milestone`, at
   level L4, and writes an audit row with outcome `refused`.
4. At L3 the server's advertised tool list does **not** contain `commit_changes`.

Test 4 is the one that proves the thesis. Do not skip it.

---

## Definition of done

- `claude mcp list` shows `agentic-pm` connected.
- `/mcp` at L3 lists exactly: `search_issues`, `get_issue`, `start_run`,
  `propose_estimate_change`, `propose_due_date_change`, `finish_run` — and not
  `commit_changes`.
- Asking Claude Code *"re-estimate the stories in AUR that have no points, and explain each"*
  produces staged changes and changes nothing in Jira.
- Approving in the browser changes the points in Jira, visible in the Jira UI.
- Rejecting changes nothing.
- The guardrail refusal is demonstrable and appears in `/audit`.
- All four tests pass.
- `scripts/demo_slice1.py` runs clean and its output is screenshot-worthy.

---

## Slice 2 preview (do not build yet)

Add L1, L2 and L4 to the same task. L1 registers no write tools. L2 renders the review
surface with editable fields. L4 registers `commit_changes` and gates the token behind
per-item acknowledgement — Approve disabled until every `acknowledged` flag is 1.

That slice is where the project becomes defensible, and it is small because slice 1 did the
hard part.

---

## Starting prompt for Claude Code

Paste this once the checklist in `SETUP.md` is complete:

```
Read CLAUDE.md and docs/slice-1.md before writing anything.

Build slice 1 in this order, pausing after each step so I can review:

1. gateway/jira.py — the Jira REST client. Auth from .env, plus a
   discover-fields helper. Then run scripts/check_connection.py against
   my real site and show me the output.
2. db/schema.sql and the migration runner.
3. gateway/guardrails.py — the milestone validator, with its test.
4. gateway/server.py — the MCP server with level-scoped tool
   registration. Start it at L3 and show me that commit_changes is absent
   from the tool list.
5. app/ — the four routes and the read-only L3 review surface.
6. scripts/seed_demo.py, then scripts/demo_slice1.py.
7. tests/.

Rules: verify against real Jira, never mocks. Nothing in gateway/ prints
to stdout. Explain each file in 2–3 sentences as you finish it. Stop and
ask if anything in the spec is ambiguous rather than guessing.
```
