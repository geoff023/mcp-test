"""Tests for app/run_display.py - readable scope, dates and labels on the
Runs / run-detail pages. Every scope string below exists in a real run row."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.run_display import friendly_scope, full_datetime, humanize, short_date


@pytest.mark.parametrize(
    "scope, expected",
    [
        ("project = MCP AND issuetype = Story AND cf[10016] is EMPTY", "Stories with no estimate yet"),
        ('project = MCP AND "Story point estimate" is EMPTY', "Stories with no estimate yet"),
        ("project = MCP AND issuetype = Story", "All stories in this project"),
        ("key in (MCP-9)", "Issue MCP-9"),
        ("key = MCP-2", "Issue MCP-2"),
        ("MCP-1", "Issue MCP-1"),
        ("MCP-4 (L2 edit test)", "Issue MCP-4"),
        ("key in (MCP-4, MCP-5, MCP-6)", "Issues MCP-4, MCP-5, MCP-6"),
    ],
)
def test_friendly_scope_reads_the_scope_in_plain_words(scope, expected):
    assert friendly_scope(scope) == expected


def test_friendly_scope_falls_back_to_the_raw_text_when_it_cannot_tell():
    assert friendly_scope("labels = urgent") == "labels = urgent"
    assert friendly_scope(None) == ""


def test_humanize_gives_sentence_case_labels():
    assert humanize("awaiting_review") == "Awaiting review"
    assert humanize("applied") == "Applied"
    assert humanize("story_points") == "Story points"


def test_dates_are_the_local_calendar_date_and_the_full_time_on_hover():
    ts = "2026-09-15T02:25:42.857704+00:00"
    local = datetime.fromisoformat(ts).astimezone()
    assert short_date(ts) == f"{local.day} {local:%b %Y}"
    assert full_datetime(ts).endswith(local.strftime("%H:%M:%S"))
    assert short_date(None) == "" and full_datetime(None) == ""
