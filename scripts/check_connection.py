"""Smoke test: prove gateway/jira.py can authenticate against the real site.

Usage: uv run python -m scripts.check_connection
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.jira import JiraClient, JiraConfigError  # noqa: E402


def main() -> int:
    try:
        client = JiraClient()
    except JiraConfigError as exc:
        print(f"Config error: {exc}")
        return 1

    with client:
        me = client.myself()
        print(f"Authenticated as: {me['displayName']} <{me['emailAddress']}>")
        print(f"accountId: {me['accountId']}")

        project = client.get_project()
        print(
            f"\nProject: {project['key']} - {project['name']} "
            f"[{project['projectTypeKey']}, {project['style']}]"
        )

        hits = client.discover_field("story point")
        print("\nStory point field(s) found via /rest/api/3/field:")
        for f in hits:
            marker = " <- configured in .env" if f["id"] == client.config.story_points_field else ""
            print(f"  {f['id']}  |  {f['name']}{marker}")

        issues = client.search_issues(f"project = {client.config.project_key} ORDER BY created DESC", max_results=5)
        print(f"\nMost recent issues in {client.config.project_key}:")
        if not issues:
            print("  (none yet)")
        for issue in issues:
            print(f"  {issue.key}  {issue.summary}  [{issue.status}]  points={issue.story_points}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
