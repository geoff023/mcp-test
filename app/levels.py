"""Product-facing display names for the L1-L4 autonomy levels.

Purely a presentation-layer mapping. The underlying level codes ("L1" ..
"L4") are unchanged everywhere else - TOOLS_BY_LEVEL, AGENTIC_PM_LEVEL,
runs.level, every test. CLAUDE.md's own names for these levels
(AI-Assisted/Operator, Human-AI Collaborative, Supervised-AI/Consultant,
Guided AI-Autonomy/Approver) come directly from Assalaarachchi et al.'s
four working modes and stay the authoritative, citable names for the
report - these are friendlier labels for the product UI, shown alongside
the level code, not a replacement of the academic ones. (Note the overlap:
"Consultant" is this product's name for L1 but the academic name for L3.)
"""

from __future__ import annotations

LEVEL_DISPLAY: dict[str, dict[str, str]] = {
    "L1": {
        "name": "Consultant",
        "tagline": "AI advises. You decide.",
    },
    "L2": {
        "name": "Co-worker",
        "tagline": "AI creates. You refine.",
    },
    "L3": {
        "name": "Committer",
        "tagline": "AI completes. You approve.",
    },
    "L4": {
        "name": "Super-Pilot",
        "tagline": "AI executes. You authorise.",
    },
}


def level_name(level: str | None) -> str:
    """The friendly product name for a level code, or the code itself if unknown."""
    return LEVEL_DISPLAY.get(level or "", {}).get("name", level or "")


def level_tagline(level: str | None) -> str:
    """One-line plain-language description of what that level actually does."""
    return LEVEL_DISPLAY.get(level or "", {}).get("tagline", "")
