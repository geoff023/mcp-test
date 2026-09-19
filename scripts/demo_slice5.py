"""Prints the dashboard's own analytics against the real Jira site - the
same aggregation (app/analytics.py) and the same Jira read
(gateway/jira.py's search_issues_for_analytics) the /dashboard route
uses, so this is evidence the numbers on screen are real, not a
separately-computed demo figure.

Usage:
    uv run python scripts/demo_slice5.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics import STATUS_CATEGORY_LABELS, STATUS_CATEGORY_ORDER, build_dashboard_data  # noqa: E402
from gateway.jira import JiraClient  # noqa: E402

APP_URL = "http://localhost:8000"


def main() -> int:
    client = JiraClient()
    with client:
        jql = f"project = {client.config.project_key} ORDER BY created ASC"
        rows = client.search_issues_for_analytics(jql)

    if not rows:
        raise SystemExit(f"No issues found for {client.config.project_key} - nothing to analyze.")

    data = build_dashboard_data(rows)

    print("=" * 72)
    print(f"SLICE 5 DEMO - dashboard analytics for {client.config.project_key}")
    print("=" * 72)

    print(f"\n1. {data.total_issues} issues total:")
    for key in STATUS_CATEGORY_ORDER:
        print(f"   {STATUS_CATEGORY_LABELS[key]:<14} {data.status_counts[key]}")

    print(
        f"\n2. Story points: {data.done_points:.1f} done / {data.total_points:.1f} total "
        f"({data.remaining_points:.1f} remaining)"
    )
    print(f"   Estimated stories: {data.estimated_count}   Unestimated: {data.unestimated_count}")

    print(f"\n3. Overdue issues: {len(data.overdue)}")
    for item in data.overdue:
        print(f"   {item['key']}  {item['days_overdue']}d overdue  - {item['summary']}")

    print(f"\n4. Upcoming milestones: {len(data.upcoming_milestones)}")
    for item in data.upcoming_milestones:
        print(f"   {item['key']}  due in {item['days_until']}d  - {item['summary']}")

    print("\n5. Story points remaining, last 21 days (label: remaining):")
    for label, remaining in data.burndown[::3]:  # every 3rd point - the console doesn't need all 21
        print(f"   {label}: {remaining}")

    if data.velocity_per_day:
        print(
            f"\n6. Forecast: ~{data.velocity_per_day:.2f} points/day recently -> "
            f"~{data.forecast_days:.1f} more day(s) for the remaining {data.remaining_points:.1f} points"
        )
    else:
        print("\n6. Forecast: not enough recently-completed stories to estimate a pace.")

    print("\n7. Suggestions:")
    for s in data.suggestions:
        print(f"   [{s.severity}] {s.text}")
        if s.action_url:
            print(f"       -> {s.action_label}: {APP_URL}{s.action_url}")

    print(f"\nFull dashboard: {APP_URL}/dashboard")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
