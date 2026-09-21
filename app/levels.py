"""Product-facing display data for the L1-L4 autonomy levels and the task types.

Purely a presentation-layer mapping. The underlying level codes ("L1" ..
"L4") are unchanged everywhere else - TOOLS_BY_LEVEL, AGENTIC_PM_LEVEL,
runs.level, every test. CLAUDE.md's own names for these levels
(AI-Assisted/Operator, Human-AI Collaborative, Supervised-AI/Consultant,
Guided AI-Autonomy/Approver) come directly from Assalaarachchi et al.'s
four working modes and stay the authoritative, citable names for the
report - these are friendlier labels for the product UI, shown alongside
the level code, not a replacement of the academic ones. (Note the overlap:
"Consultant" is this product's name for L1 but the academic name for L3.)

`creates` / `writes` say, in plain words, what a level produces and when it
may reach Jira - the same facts CLAUDE.md section 2 states, shown to the user
on the New task and Settings pages.
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
        "writes": "After you acknowledge every change and issue a token",
    },
}

# The task types the New task page offers. Only `reestimate` is built; the rest
# are visible-but-disabled, each already carrying its recommended level so the
# recommendation is correct the moment it ships. Higher-risk or higher-blast-radius
# task types recommend a lower autonomy level; reestimate at L2 matches the academic
# framework's own worked example for Human-AI Collaborative (CLAUDE.md section 2).
TASK_TYPES: list[dict] = [
    {"slug": "reestimate", "label": "Effort estimation", "recommended": "L2", "available": True},
    {"slug": "weekly_status", "label": "Weekly status report", "recommended": "L3", "available": False},
    {"slug": "standup_digest", "label": "Standup / meeting digest", "recommended": "L3", "available": False},
    {"slug": "risk_scan", "label": "Risk scan / report", "recommended": "L1", "available": False},
    {"slug": "sprint_planning", "label": "Sprint planning", "recommended": "L1", "available": False},
    {"slug": "retrospective", "label": "Retrospective synthesis", "recommended": "L2", "available": False},
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
    """The friendly product name for a level code, or the code itself if unknown."""
    return LEVEL_DISPLAY.get(level or "", {}).get("name", level or "")


def level_tagline(level: str | None) -> str:
    """One-line plain-language description of what that level actually does."""
    return LEVEL_DISPLAY.get(level or "", {}).get("tagline", "")


def tool_label(tool: str) -> str:
    return TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize())
