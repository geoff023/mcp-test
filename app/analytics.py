"""Pure aggregation, forecasting, and rule-based suggestions for the
project dashboard (GET /dashboard, app/main.py). Takes the raw rows from
gateway.jira.JiraClient.search_issues_for_analytics() and returns plain
data structures the template renders - no Jira calls and no randomness in
here, so it is fully unit-testable against a fixed list of rows (see
tests/test_analytics.py) without a live site.

The forecast is a plain historical-velocity heuristic - remaining points
divided by a trailing average of points resolved per day - not a
statistical model. Said plainly rather than dressed up, because a demo
project with a handful of issues cannot support more than that; an
LLM-driven version (reading the same rows and reasoning about risk) is
future work, not built here - see docs/slice-5.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

STATUS_CATEGORY_LABELS = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
STATUS_CATEGORY_ORDER = ["new", "indeterminate", "done"]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


@dataclass
class Suggestion:
    severity: str  # "warning" | "info"
    text: str
    action_url: str | None = None
    action_label: str | None = None


@dataclass
class DashboardData:
    total_issues: int
    status_counts: dict[str, int]
    total_points: float
    done_points: float
    remaining_points: float
    unestimated_count: int
    estimated_count: int
    overdue: list[dict]
    upcoming_milestones: list[dict]
    burndown: list[tuple[str, float]]
    velocity_per_day: float | None
    forecast_days: float | None
    suggestions: list[Suggestion]


def build_dashboard_data(rows: list[dict], *, today: date | None = None, burndown_days: int = 21) -> DashboardData:
    today = today or datetime.now(timezone.utc).date()

    status_counts = {k: 0 for k in STATUS_CATEGORY_ORDER}
    for row in rows:
        category = row.get("status_category") or "new"
        status_counts[category] = status_counts.get(category, 0) + 1

    stories = [r for r in rows if r.get("issue_type") == "Story"]
    estimated = [r for r in stories if r.get("story_points") is not None]
    unestimated = [r for r in stories if r.get("story_points") is None]
    total_points = float(sum(r["story_points"] for r in estimated))
    done_points = float(sum(r["story_points"] for r in estimated if r.get("status_category") == "done"))
    remaining_points = total_points - done_points

    overdue: list[dict] = []
    upcoming_milestones: list[dict] = []
    for row in rows:
        due = _parse_date(row.get("due_date"))
        if due is None:
            continue
        if row.get("status_category") != "done" and due < today:
            overdue.append(
                {
                    "key": row["key"],
                    "summary": row.get("summary", ""),
                    "due_date": row["due_date"],
                    "days_overdue": (today - due).days,
                }
            )
        if "milestone" in (row.get("labels") or []) and due >= today:
            upcoming_milestones.append(
                {
                    "key": row["key"],
                    "summary": row.get("summary", ""),
                    "due_date": row["due_date"],
                    "days_until": (due - today).days,
                }
            )
    overdue.sort(key=lambda x: -x["days_overdue"])
    upcoming_milestones.sort(key=lambda x: x["days_until"])

    burndown, velocity_per_day = _burndown_and_velocity(
        estimated, total_points=total_points, today=today, window_days=burndown_days
    )

    forecast_days = None
    if velocity_per_day and remaining_points > 0:
        forecast_days = remaining_points / velocity_per_day

    suggestions = _build_suggestions(
        unestimated=unestimated,
        overdue=overdue,
        upcoming_milestones=upcoming_milestones,
        remaining_points=remaining_points,
        velocity_per_day=velocity_per_day,
        forecast_days=forecast_days,
    )

    return DashboardData(
        total_issues=len(rows),
        status_counts=status_counts,
        total_points=total_points,
        done_points=done_points,
        remaining_points=remaining_points,
        unestimated_count=len(unestimated),
        estimated_count=len(estimated),
        overdue=overdue,
        upcoming_milestones=upcoming_milestones,
        burndown=burndown,
        velocity_per_day=velocity_per_day,
        forecast_days=forecast_days,
        suggestions=suggestions,
    )


def _burndown_and_velocity(
    estimated: list[dict], *, total_points: float, today: date, window_days: int
) -> tuple[list[tuple[str, float]], float | None]:
    """Not a sprint burndown (this prototype has no sprint start/end dates
    - see CLAUDE.md section 6 on the Agile API being separate and out of
    scope). Instead: cumulative story points resolved by day, turned into
    "remaining scope over time" for the trailing window - an honest
    approximation built from real resolution dates, not fabricated data.
    """
    resolved_by_day: dict[date, float] = defaultdict(float)
    for row in estimated:
        resolved_date = _parse_date(row.get("resolved_at"))
        if resolved_date is not None and row.get("status_category") == "done":
            resolved_by_day[resolved_date] += row["story_points"]

    window_start = today - timedelta(days=window_days - 1)
    cumulative = sum(v for d, v in resolved_by_day.items() if d < window_start)
    burndown: list[tuple[str, float]] = []
    for i in range(window_days):
        day = window_start + timedelta(days=i)
        cumulative += resolved_by_day.get(day, 0.0)
        remaining = max(total_points - cumulative, 0.0)
        burndown.append((day.strftime("%b %d"), round(remaining, 1)))

    resolved_in_window = [(d, v) for d, v in resolved_by_day.items() if d >= window_start]
    if not resolved_in_window:
        return burndown, None
    span_days = (today - min(d for d, _ in resolved_in_window)).days + 1
    velocity_per_day = sum(v for _, v in resolved_in_window) / max(span_days, 1)
    return burndown, velocity_per_day


def _build_suggestions(
    *,
    unestimated: list[dict],
    overdue: list[dict],
    upcoming_milestones: list[dict],
    remaining_points: float,
    velocity_per_day: float | None,
    forecast_days: float | None,
) -> list[Suggestion]:
    out: list[Suggestion] = []

    if unestimated:
        out.append(
            Suggestion(
                severity="info",
                text=f"{len(unestimated)} stor{'y is' if len(unestimated) == 1 else 'ies are'} not yet estimated.",
                action_url="/agent-console?scope=unestimated",
                action_label="Estimate them now",
            )
        )

    if overdue:
        out.append(
            Suggestion(
                severity="warning",
                text=f"{len(overdue)} issue{'s are' if len(overdue) != 1 else ' is'} past its due date and not yet Done.",
            )
        )

    for milestone in upcoming_milestones:
        if milestone["days_until"] > 7:
            continue
        if forecast_days is not None and forecast_days > milestone["days_until"]:
            out.append(
                Suggestion(
                    severity="warning",
                    text=(
                        f"Milestone {milestone['key']} is due in {milestone['days_until']} day(s), but "
                        f"remaining work is on pace to take about {forecast_days:.0f} more day(s) at the "
                        "current rate."
                    ),
                )
            )
        else:
            out.append(
                Suggestion(severity="info", text=f"Milestone {milestone['key']} is due in {milestone['days_until']} day(s).")
            )

    if remaining_points > 0 and velocity_per_day is None:
        out.append(
            Suggestion(
                severity="info",
                text="Not enough recently-completed stories to estimate a pace yet - a forecast will "
                "appear once some are marked Done.",
            )
        )

    if not out:
        return [
            Suggestion(
                severity="info",
                text="Nothing urgent - no overdue issues, no unestimated stories, no milestones due soon.",
            )
        ]

    out.sort(key=lambda s: 0 if s.severity == "warning" else 1)
    return out
