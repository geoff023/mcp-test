"""Tests for app/analytics.py - pure aggregation/forecast/suggestion logic
over a fixed list of rows, no Jira call involved (that's
gateway.jira.JiraClient.search_issues_for_analytics()'s job, exercised
live by scripts/demo_slice5.py instead)."""

from __future__ import annotations

from datetime import date

from app.analytics import build_dashboard_data

TODAY = date(2026, 9, 19)


def _row(key, *, status_category="new", issue_type="Story", points=None, due=None, resolved=None, labels=None):
    return {
        "key": key,
        "summary": f"Summary for {key}",
        "status": "placeholder",
        "status_category": status_category,
        "issue_type": issue_type,
        "story_points": points,
        "due_date": due,
        "resolved_at": resolved,
        "labels": labels or [],
    }


def test_status_counts_cover_all_three_categories_even_when_empty():
    data = build_dashboard_data([_row("A", status_category="done")], today=TODAY)

    assert data.status_counts == {"new": 0, "indeterminate": 0, "done": 1}
    assert data.total_issues == 1


def test_points_only_counted_for_stories_not_epics():
    rows = [
        _row("EPIC-1", issue_type="Epic", points=40, status_category="new"),
        _row("S-1", issue_type="Story", points=3, status_category="done"),
        _row("S-2", issue_type="Story", points=5, status_category="new"),
    ]
    data = build_dashboard_data(rows, today=TODAY)

    assert data.total_points == 8
    assert data.done_points == 3
    assert data.remaining_points == 5
    assert data.estimated_count == 2
    assert data.unestimated_count == 0


def test_unestimated_stories_are_counted_and_excluded_from_points():
    rows = [_row("S-1", points=None), _row("S-2", points=2, status_category="done")]
    data = build_dashboard_data(rows, today=TODAY)

    assert data.unestimated_count == 1
    assert data.estimated_count == 1
    assert data.total_points == 2


def test_overdue_only_counts_not_done_issues_past_their_due_date():
    rows = [
        _row("A", status_category="new", due="2026-09-01"),  # overdue
        _row("B", status_category="done", due="2026-09-01"),  # done, not overdue
        _row("C", status_category="new", due="2026-12-01"),  # future, not overdue
    ]
    data = build_dashboard_data(rows, today=TODAY)

    assert [o["key"] for o in data.overdue] == ["A"]
    assert data.overdue[0]["days_overdue"] == 18


def test_upcoming_milestones_need_the_milestone_label_and_a_future_due_date():
    rows = [
        _row("M-1", due="2026-09-22", labels=["milestone"]),  # 3 days out
        _row("M-2", due="2026-08-01", labels=["milestone"]),  # already past - not "upcoming"
        _row("S-1", due="2026-09-22", labels=[]),  # no milestone label
    ]
    data = build_dashboard_data(rows, today=TODAY)

    assert [m["key"] for m in data.upcoming_milestones] == ["M-1"]
    assert data.upcoming_milestones[0]["days_until"] == 3


def test_burndown_window_length_matches_the_requested_number_of_days():
    data = build_dashboard_data([_row("S-1", points=5)], today=TODAY, burndown_days=7)

    assert len(data.burndown) == 7
    assert data.burndown[-1][1] == 5  # nothing resolved - all 5 points still remaining today


def test_burndown_decreases_as_points_are_resolved_and_velocity_is_computed():
    rows = [
        _row("S-1", points=3, status_category="done", resolved="2026-09-17T10:00:00.000+0000"),
        _row("S-2", points=2, status_category="new"),
    ]
    data = build_dashboard_data(rows, today=TODAY, burndown_days=5)

    # window: Sep 15..19. Before Sep 17, remaining = 5 (nothing resolved yet).
    labels_values = dict(data.burndown)
    assert labels_values["Sep 15"] == 5
    assert labels_values["Sep 16"] == 5
    assert labels_values["Sep 17"] == 2  # 3 points resolved that day
    assert labels_values["Sep 19"] == 2
    assert data.velocity_per_day is not None
    assert data.velocity_per_day > 0


def test_no_resolution_activity_means_no_velocity_and_no_forecast():
    data = build_dashboard_data([_row("S-1", points=5, status_category="new")], today=TODAY)

    assert data.velocity_per_day is None
    assert data.forecast_days is None


def test_forecast_is_none_once_everything_is_already_done():
    rows = [_row("S-1", points=3, status_category="done", resolved="2026-09-18T00:00:00.000+0000")]
    data = build_dashboard_data(rows, today=TODAY)

    assert data.remaining_points == 0
    assert data.forecast_days is None


def test_unestimated_suggestion_links_to_the_prefilled_agent_console():
    data = build_dashboard_data([_row("S-1", points=None), _row("S-2", points=None)], today=TODAY)

    suggestion = next(s for s in data.suggestions if "not yet estimated" in s.text)
    assert suggestion.severity == "info"
    assert suggestion.action_url == "/agent-console?scope=unestimated"
    assert "2 stories" in suggestion.text


def test_overdue_produces_a_warning_suggestion():
    data = build_dashboard_data([_row("A", status_category="new", due="2026-09-01")], today=TODAY)

    suggestion = next(s for s in data.suggestions if "past its due date" in s.text)
    assert suggestion.severity == "warning"


def test_milestone_at_risk_warns_when_forecast_exceeds_days_until_due():
    rows = [
        _row("M-1", due="2026-09-21", labels=["milestone"]),  # 2 days out
        _row("S-1", points=20, status_category="new"),
        _row(
            "S-2",
            points=1,
            status_category="done",
            resolved="2026-09-18T00:00:00.000+0000",
        ),  # tiny recent velocity -> forecast way more than 2 days
    ]
    data = build_dashboard_data(rows, today=TODAY)

    warning = next(s for s in data.suggestions if "M-1" in s.text)
    assert warning.severity == "warning"
    assert "on pace to take about" in warning.text


def test_warnings_sort_before_info_suggestions():
    rows = [
        _row("S-1", points=None),  # info: unestimated
        _row("A", status_category="new", due="2026-09-01"),  # warning: overdue
    ]
    data = build_dashboard_data(rows, today=TODAY)

    assert data.suggestions[0].severity == "warning"


def test_nothing_urgent_fallback_when_there_are_no_suggestions():
    data = build_dashboard_data([_row("S-1", points=3, status_category="done", resolved="2026-09-18T00:00:00.000+0000")], today=TODAY)

    assert len(data.suggestions) == 1
    assert "Nothing urgent" in data.suggestions[0].text
