# Slice 5 — dashboard, audit log clarity, and flow polish

**Built and verified live** against the real Jira site — see `scripts/demo_slice5.py`. 104 tests
passing (64 pre-existing plus 40 new, across `tests/test_analytics.py`, `tests/test_charts.py`,
`tests/test_audit_display.py`, `tests/test_audit_route.py`, `tests/test_dashboard_route.py`, plus
additions to `tests/test_agent_console.py`).

**Goal:** three requested improvements, bundled because none stands alone: reduce navigation
friction and make the audit log scannable, then add a project-analytics dashboard that itself
needs somewhere to link its suggestions back into (the Agent console, pre-filled).

---

## 1. Flow and click reduction

- Nav reordered (slice 4.5, prior session) already put the two entry points first; this slice adds
  small inline SVG icons to every nav item (`base.html`) and a fifth item, **Dashboard**.
- `GET /agent-console` now accepts `?scope=` and pre-selects the matching "which issues?" radio
  (`app/main.py`'s `agent_console` route). The dashboard's "N stories not yet estimated"
  suggestion links straight to `/agent-console?scope=unestimated` - the human doesn't re-answer a
  question they were just given the answer to.
- The L4 token box gets a **Copy** button (`navigator.clipboard`, vanilla JS - same minimal-JS
  pattern as the existing L1-redirect script) instead of requiring manual select-all.
- A resolved run (`applied`/`rejected`) now shows "Start another run · See it reflected on the
  dashboard" links, instead of leaving the human to find the nav themselves.
- `app/main.py`'s new `_status_note()` (already added the prior session) and this slice's changes
  together mean every page states what happened and what's next, not just a bare status pill.

## 2. Audit log clarity

**Problem:** the log was a flat 7-column table of raw technical strings - `propose_estimate_change`,
`agent:orchestrator:gemini-3.6-flash`, a Python list repr in the detail column. Correct, but not
scannable.

**Change:** `app/audit_display.py` (new) provides `friendly_action()` (action code → plain
English, e.g. `commit_changes` → "Applied to Jira"), `friendly_actor()` (actor string → a name a
reviewer recognises - importantly, `agent:planning` becomes "Human (via Claude Code)", not
something that reads as AI, since that's literally a human typing into Claude Code by hand - see
`gateway/server.py`'s `DEFAULT_AGENT` docstring), `outcome_icon()` (✓ / ✕ / • by outcome
category), and `group_audit_rows()` (consecutive same-run rows collapsed into one entry).
`app/templates/audit.html` renders each run as a native `<details>`/`<summary>` disclosure - zero
JavaScript, the browser does the collapse/expand - showing a one-line summary (latest action,
outcome, level) with the individual steps available on click. A run with only one logged action
renders as a plain row, not a one-item disclosure that adds a click for nothing. Also fixed two
outcome values (`awaiting_review`, `answered`/`failed` from chat turns) that had no colour rule at
all before this slice - a real colour-consistency gap, not just a redesign choice.

## 3. Dashboard

**Design constraint carried over from CLAUDE.md section 4:** no chart library, no build step. All
three charts (`app/charts.py`) are hand-built inline `<svg>` - a bar chart, a donut, and a
line/area chart - using `var(--brand)`/`var(--blue)`/`var(--green)`/etc. directly as SVG
`fill`/`stroke` values. This works (and stays in sync with every pill/status colour elsewhere)
because the SVG renders inline in the page's own DOM, not as a separate image, so the same
`:root` custom properties `app/static/style.css` already defines apply to it.

`gateway/jira.py` gained `search_issues_for_analytics()` - a second, separate read method from
`search_issues()`, fetching `resolutiondate`/`created`/status-category on top of what the MCP
tools' `search_issues` reads. Kept separate deliberately: `Issue`/`_issue_summary()` is a fixed
contract the gateway's tool payloads and their tests depend on; analytics needed more fields
without touching it.

`app/analytics.py`'s `build_dashboard_data()` is pure (no Jira call, no randomness - see
`tests/test_analytics.py`) and computes:

- Status breakdown (To Do / In Progress / Done) - all issue types.
- Story points: total / done / remaining, and estimated-vs-unestimated story counts - Stories
  only, Epics excluded from points stats deliberately (they don't carry story-level estimates in
  this project's data).
- Overdue issues (due date passed, not Done) and upcoming milestone-labelled issues.
- A "story points remaining over time" chart built from each story's actual `resolutiondate` -
  explicitly **not** called a sprint burndown in the UI, because this prototype has no sprint
  start/end dates (CLAUDE.md section 6: the Agile API is separate and out of scope). Said plainly
  in the dashboard's own caption rather than overclaiming.
- A forecast: remaining points ÷ a trailing velocity (points resolved per day, recent window).
  Named for what it is - a pace-based heuristic from a handful of data points, not a statistical
  model - both in the docstring and in an on-page disclaimer, so it can't be mistaken for more
  rigor than the data supports.
- Rule-based suggestions (not LLM-driven - deterministic, free, and testable; an LLM-driven
  version reading the same rows is future work, not this slice) - warns when a milestone's due
  date is closer than the forecast suggests it'll actually be met, when issues are overdue, or
  when there isn't yet enough resolution history to forecast at all; links the "N unestimated"
  suggestion straight to the pre-filled Agent console.

Four stat cards (total issues, points done/total, unestimated count, overdue count) use the same
icon-badge pattern as the rest of the app, colour-coded amber/red only when there's actually
something to flag - neutral grey otherwise, so the colour itself carries meaning rather than
being decoration.

---

## 4. Definition of done

- [x] Runs against the real Jira site - `scripts/demo_slice5.py` prints the same aggregates the
      dashboard shows, computed from a live `search_issues_for_analytics()` call.
- [x] Refusal-shaped test: none of this slice's code can write to Jira - there is no write call
      anywhere in `app/analytics.py`, `app/charts.py`, or the `/dashboard` route, which
      `tests/test_dashboard_route.py` implicitly confirms (the mocked Jira spy is never asked to
      write, only searched).
- [x] No new gateway tools - `TOOLS_BY_LEVEL` is unchanged; this slice is entirely `app/`.
- [x] Every file explainable without re-reading - see sections 1-3 above.
