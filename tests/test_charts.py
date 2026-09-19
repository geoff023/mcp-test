"""Tests for app/charts.py - the inline-SVG chart builders. Checks
structural correctness (one shape per data point, valid SVG root) and
that untrusted-looking label text is escaped, not that it's pixel
perfect - this isn't a real charting library."""

from __future__ import annotations

from markupsafe import Markup

from app.charts import area_chart, bar_chart, donut_chart


def test_bar_chart_draws_one_rect_per_bar():
    svg = bar_chart(
        [
            {"label": "To Do", "value": 3, "color": "var(--text-muted)"},
            {"label": "Done", "value": 5, "color": "var(--green)"},
        ]
    )

    assert isinstance(svg, Markup)
    assert svg.count("<rect") == 2
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_bar_chart_with_no_bars_is_still_a_valid_empty_svg():
    svg = bar_chart([])

    assert svg.startswith("<svg")
    assert "<rect" not in svg


def test_bar_chart_escapes_label_text():
    svg = bar_chart([{"label": "<script>alert(1)</script>", "value": 1, "color": "var(--red)"}])

    assert "<script>alert" not in svg
    assert "&lt;script&gt;" in svg


def test_donut_chart_draws_one_circle_per_positive_segment():
    svg = donut_chart(
        [
            {"label": "Estimated", "value": 4, "color": "var(--brand)"},
            {"label": "Unestimated", "value": 0, "color": "var(--border)"},  # zero - should not draw a circle
        ]
    )

    # one background ring + one segment circle for the positive-value segment only
    assert svg.count("<circle") == 2


def test_donut_chart_shows_center_value_and_label_when_given():
    svg = donut_chart([{"label": "Estimated", "value": 1, "color": "var(--brand)"}], center_label="estimated", center_value="80%")

    assert "80%" in svg
    assert "estimated" in svg


def test_donut_chart_with_zero_total_still_renders_the_background_ring():
    svg = donut_chart([{"label": "A", "value": 0, "color": "var(--brand)"}])

    assert svg.count("<circle") == 1


def test_area_chart_draws_a_line_and_fill_path():
    points = [{"label": "Sep 01", "value": 10}, {"label": "Sep 02", "value": 8}, {"label": "Sep 03", "value": 5}]
    svg = area_chart(points)

    assert svg.count("<path") == 2  # fill area + line
    assert svg.startswith("<svg")


def test_area_chart_with_no_points_is_still_a_valid_empty_svg():
    svg = area_chart([])

    assert svg.startswith("<svg")
    assert "<path" not in svg


def test_area_chart_escapes_label_text():
    svg = area_chart([{"label": "<b>hi</b>", "value": 1}])

    assert "<b>hi</b>" not in svg
    assert "&lt;b&gt;" in svg
