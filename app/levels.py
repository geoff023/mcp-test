"""Product-facing display names for the L1-L4 autonomy levels.

Purely a presentation-layer mapping. The underlying level codes ("L1" ..
"L4") are unchanged everywhere else - TOOLS_BY_LEVEL, AGENTIC_PM_LEVEL,
runs.level, every test. CLAUDE.md's own names for these levels
(AI-Assisted/Operator, Human-AI Collaborative, Supervised-AI/Consultant,
Guided AI-Autonomy/Approver) come directly from Assalaarachchi et al.'s
four working modes and stay the authoritative, citable names for the
report - these are friendlier labels for the product UI, shown alongside
the level code, not a replacement of the academic ones.
"""

from __future__ import annotations

LEVEL_DISPLAY: dict[str, dict[str, str]] = {
    "L1": {
        "name": "Advisor",
        "tagline": "Reads and suggests - you do everything yourself",
    },
    "L2": {
        "name": "Co-Pilot",
        "tagline": "Drafts proposals - you can edit before anything is sent",
    },
    "L3": {
        "name": "Drafter",
        "tagline": "Prepares a finished proposal - you approve it or send it back",
    },
    "L4": {
        "name": "Autopilot",
        "tagline": "Applies changes itself, once you authorise it",
    },
}


def level_name(level: str | None) -> str:
    """The friendly product name for a level code, or the code itself if unknown."""
    return LEVEL_DISPLAY.get(level or "", {}).get("name", level or "")


def level_tagline(level: str | None) -> str:
    """One-line plain-language description of what that level actually does."""
    return LEVEL_DISPLAY.get(level or "", {}).get("tagline", "")
