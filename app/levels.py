"""Product-facing display data for the L1-L4 autonomy levels, the 3-mode
milestone redesign on top of them, and the task types.

Purely a presentation-layer mapping. The underlying level codes ("L1" ..
"L4") are unchanged everywhere else - TOOLS_BY_LEVEL, AGENTIC_PM_LEVEL,
runs.level, every test. CLAUDE.md's own names for these levels
(AI-Assisted/Operator, Human-AI Collaborative, Supervised-AI/Consultant,
Guided AI-Autonomy/Approver) come directly from Assalaarachchi et al.'s
four working modes and stay the authoritative, citable names for the
report - LEVEL_DISPLAY are friendlier labels for the Settings page only now
(see below), not a replacement of the academic ones.

Milestone redesign: the product surface collapsed from 4 levels to 3 modes
(M1 Co-Pilot, M2 Super-Pilot, M3 Auto-Pilot), per the supervisor's framework
revision - see MODE_DISPLAY/MODES. M2 folds L2 and L3 together (both are
"propose an editable draft, human decides before it writes" from a user's
perspective - CLAUDE.md already noted L2 vs L3 was UI-enforced, not a tool-
layer distinction). L3 stays fully wired in the backend/tests as the locked-
review variant; it simply has no nav entry point of its own any more.
Settings is the one page that still shows the true L1-L4 breakdown, on
purpose - it is the transparency page, and collapsing it to 3 modes would
hide that L2 and L3 are, honestly, two different UI-enforced variants,
not one.

`creates` / `writes` say, in plain words, what a level produces and when it
may reach Jira - the same facts CLAUDE.md section 2 states, shown to the user
on the Settings page.
"""

from __future__ import annotations

LEVEL_DISPLAY: dict[str, dict[str, str]] = {
    "L1": {
        "name": "Consultant",
        "tagline": "AI advises. You decide.",
        "creates": "An answer in chat",
        "writes": "Never. It has no write tools at all.",
    },
    "L2": {
        "name": "Co-worker",
        "tagline": "AI creates. You refine.",
        "creates": "A draft you can edit",
        "writes": "After you edit it and press Approve",
    },
    "L3": {
        "name": "Committer",
        "tagline": "AI completes. You approve.",
        "creates": "A finished proposal, locked",
        "writes": "After you press Approve",
    },
    "L4": {
        "name": "Super-Pilot",
        "tagline": "AI executes. You authorise.",
        "creates": "A batch of proposals",
        "writes": "After you authorise the batch, then Approve",
    },
}

# The task types the New task page offers. Only `reestimate` is built; the rest
# are visible-but-disabled, each already carrying its recommended level so the
# recommendation is correct the moment it ships. Higher-risk or higher-blast-radius
# task types recommend a lower autonomy level; reestimate at L2 matches the academic
# framework's own worked example for Human-AI Collaborative (CLAUDE.md section 2).
# `recommended` is an L-code (drives the coloured dot, still keyed per level), but
# never L3 - since M2 (Super-Pilot) now display-merges L2+L3, a task recommending
# "the Super-Pilot mode" always says so as L2, so the console's one legend never
# shows the same mode name twice in two different dot colours.
TASK_TYPES: list[dict] = [
    {
        "slug": "reestimate", "label": "Effort estimation", "recommended": "L2", "available": True,
        "reads": "Backlog stories, current estimates", "proposes": "Story point values",
    },
    {
        "slug": "weekly_status", "label": "Weekly status report", "recommended": "L2", "available": False,
        "reads": "Sprint issues, statuses, due dates", "proposes": "Status report draft",
    },
    {
        "slug": "standup_digest", "label": "Standup / meeting digest", "recommended": "L2", "available": False,
        "reads": "Recent activity, comments", "proposes": "Digest draft",
    },
    {
        "slug": "risk_scan", "label": "Risk scan / report", "recommended": "L1", "available": False,
        "reads": "Issue links, dependencies, comments", "proposes": "Risk flags and notes",
    },
    {
        "slug": "sprint_planning", "label": "Sprint planning", "recommended": "L1", "available": False,
        "reads": "Backlog, velocity, capacity", "proposes": "Sprint scope changes",
    },
    {
        "slug": "retrospective", "label": "Retrospective synthesis", "recommended": "L2", "available": False,
        "reads": "Sprint data, comments", "proposes": "Retrospective summary draft",
    },
]

READ_TOOLS = {"search_issues", "get_issue"}
TOOL_LABELS = {
    "search_issues": "Search issues",
    "get_issue": "Read an issue",
    "start_run": "Start a run",
    "propose_estimate_change": "Propose an estimate",
    "propose_due_date_change": "Propose a due date",
    "finish_run": "Finish a run",
    "commit_changes": "Apply changes to Jira",
}


def level_name(level: str | None) -> str:
    """The friendly product name for a level code, or the code itself if unknown.
    Used on the Settings page only now - everywhere else uses mode_name()."""
    return LEVEL_DISPLAY.get(level or "", {}).get("name", level or "")


def level_tagline(level: str | None) -> str:
    """One-line plain-language description of what that level actually does."""
    return LEVEL_DISPLAY.get(level or "", {}).get("tagline", "")


def tool_label(tool: str) -> str:
    return TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize())


# ---------- the 3-mode redesign ----------

MODE_DISPLAY: dict[str, dict[str, str]] = {
    "L1": {"mode": "M1", "name": "Co-Pilot", "tagline": "Agent assists when you ask, through a chat interface."},
    "L2": {
        "mode": "M2", "name": "Super-Pilot",
        "tagline": "Agent proposes an editable draft. You approve, reject or ask for a rerun before it writes.",
    },
    "L3": {
        "mode": "M2", "name": "Super-Pilot",
        "tagline": "Agent proposes an editable draft. You approve, reject or ask for a rerun before it writes.",
    },
    "L4": {"mode": "M3", "name": "Auto-Pilot", "tagline": "Agent runs on a rule you authorise, then you approve the batch to write it."},
}

# The 3 top-level entry points shown in the nav. Each dispatches to one L-code
# from TOOLS_BY_LEVEL - the tool-layer enforcement underneath is unchanged,
# this only reshapes the front door onto it. `level` is which L-code a click
# on this mode actually starts a run at (M1 -> /chat itself handles L1 vs.
# an Execute hand-off to M2; M2 and M3 dispatch directly).
MODES: list[dict] = [
    {
        "code": "M1", "name": "Co-Pilot", "href": "/chat", "level": "L1",
        "tagline": "Agent assists when you ask, through a chat interface with two sub-modes.",
        "description": "Consult: you lead and carry out every action yourself. Execute: the agent drafts, you edit.",
    },
    {
        "code": "M2", "name": "Super-Pilot", "href": "/agent-console", "level": "L2",
        "tagline": "Agent proposes an editable draft for a batch of issues.",
        "description": "You approve, reject or ask for a rerun before anything is written to Jira.",
    },
    {
        "code": "M3", "name": "Auto-Pilot", "href": "/autopilot", "level": "L4",
        "tagline": "Agent runs the task automatically once you've set it up.",
        "description": "You authorise the batch, then Approve to write it - nothing commits unattended.",
    },
]


def mode_name(level: str | None) -> str:
    """The mode name (Co-Pilot / Super-Pilot / Auto-Pilot) a level displays as
    everywhere outside Settings. Presentation only, same guarantee as
    level_name() - runs.level, TOOLS_BY_LEVEL etc. never see this value."""
    return MODE_DISPLAY.get(level or "", {}).get("name", level_name(level))


def mode_tagline(level: str | None) -> str:
    return MODE_DISPLAY.get(level or "", {}).get("tagline", "")


def mode_code(level: str | None) -> str:
    """"L2" -> "M2", for grouping/aggregating runs by mode."""
    return MODE_DISPLAY.get(level or "", {}).get("mode", level or "")
