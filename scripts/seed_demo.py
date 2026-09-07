"""Seeds realistic demo data into the real Jira project: one Epic labelled
'milestone' (for the guardrail refusal demo) and seven Stories, four of
them left unestimated so there's something for the agent to re-estimate.

Not idempotent - re-running creates a fresh batch of issues. That's fine
for a demo dataset; slice 1 has no issue-deletion tool by design.

Usage: uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.jira import JiraClient, text_to_adf  # noqa: E402

STORIES: list[tuple[str, str, float | None]] = [
    ("Add OAuth login flow", "Support Google and GitHub sign-in on the login page.", None),
    ("Implement rate limiting on the public API", "Prevent abuse of unauthenticated endpoints.", None),
    ("Write onboarding docs", "First-run guide for new project members.", 2.0),
    ("Refactor notification service", "Split email and in-app notifications into separate workers.", None),
    ("Add dark mode toggle", "User-facing theme switch, persisted per account.", 3.0),
    ("Set up CI pipeline for staging", "Auto-deploy main to the staging environment on merge.", None),
    ("Clean up stale feature flags", "Remove flags that have been at 100% rollout for 90+ days.", 1.0),
]


def _issue_type_id(issue_types: list[dict], name: str) -> str:
    for issue_type in issue_types:
        if issue_type["name"].lower() == name.lower():
            return issue_type["id"]
    available = [it["name"] for it in issue_types]
    raise SystemExit(f"No issue type named {name!r} in this project. Available: {available}")


def main() -> int:
    client = JiraClient()
    with client:
        issue_types = client.list_issue_types()
        epic_id = _issue_type_id(issue_types, "Epic")
        story_id = _issue_type_id(issue_types, "Story")

        project_key = client.config.project_key
        points_field = client.config.story_points_field
        due_date = (date.today() + timedelta(days=45)).isoformat()

        epic = client.create_issue(
            {
                "project": {"key": project_key},
                "issuetype": {"id": epic_id},
                "summary": "Q1 platform migration",
                "description": text_to_adf(
                    "Milestone epic used to demonstrate the no_milestone_date_change guardrail."
                ),
                "labels": ["milestone"],
                "duedate": due_date,
            }
        )
        print(f"Created {epic['key']}  Epic  labels=['milestone']  due={due_date}")

        for summary, description, points in STORIES:
            fields = {
                "project": {"key": project_key},
                "issuetype": {"id": story_id},
                "summary": summary,
                "description": text_to_adf(description),
            }
            if points is not None:
                fields[points_field] = points
            issue = client.create_issue(fields)
            label = f"{points} pts" if points is not None else "unestimated"
            print(f"Created {issue['key']}  Story  {label}  - {summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
