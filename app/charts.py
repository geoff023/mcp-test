"""Server-rendered inline SVG charts for the dashboard - no charting
library, no build step (CLAUDE.md section 4: plain CSS, no build step).
Each function returns a Markup string of a small, self-contained <svg>.
Colors are passed as CSS custom properties (e.g. "var(--viz-doing)") and
resolve correctly because these charts render inline in the page's own
DOM rather than as a separate image - the same :root tokens
app/static/style.css defines for status/level pills apply here too, so a
chart can never drift out of sync with the rest of the app's palette.

Each bar/segment/point carries its own label and value; app/main.py's
dashboard route builds that data once and hands it to both these
functions (for the <svg>) and the template (for a matching HTML legend),
so the two are never at risk of disagreeing.
"""

from __future__ import annotations

import math

from markupsafe import Markup, escape


def _esc(value: object) -> str:
    return str(escape(str(value)))


def _fmt(value: float) -> str:
    """Trim to at most 2 decimals, no trailing zeros - keeps generated
    SVG coordinate/value text short and readable."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def bar_chart(bars: list[dict], *, width: int = 440, height: int = 220) -> Markup:
    """bars: [{"label": str, "value": float, "color": css-color}, ...].
    Vertical bars; the value is labelled above each bar, the category
    label below it."""
    if not bars:
        return Markup(f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"></svg>')

    pad_left, pad_right, pad_top, pad_bottom = 16, 16, 26, 28
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    max_val = max((b["value"] for b in bars), default=0) or 1
    n = len(bars)
    gap = 22
    bar_w = (plot_w - gap * (n - 1)) / n
    baseline = pad_top + plot_h

    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Bar chart of issues by status">',
        f'<line x1="{pad_left}" y1="{baseline}" x2="{pad_left + plot_w}" y2="{baseline}" '
        f'stroke="var(--border)" stroke-width="1"/>',
    ]
    for i, bar in enumerate(bars):
        value = bar["value"]
        bar_h = (value / max_val) * plot_h if max_val else 0
        x = pad_left + i * (bar_w + gap)
        y = baseline - bar_h
        cx = x + bar_w / 2
        parts.append(
            f'<g><title>{_esc(bar["label"])}: {_esc(value)}</title>'
            f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(bar_w)}" height="{_fmt(bar_h)}" '
            f'rx="4" fill="{bar["color"]}"/>'
            f'<text x="{_fmt(cx)}" y="{_fmt(y - 8)}" text-anchor="middle" font-size="12" '
            f'font-weight="700" fill="var(--text)">{_esc(value)}</text>'
            f'<text x="{_fmt(cx)}" y="{_fmt(baseline + 18)}" text-anchor="middle" font-size="11" '
            f'fill="var(--text-muted)">{_esc(bar["label"])}</text>'
            f"</g>"
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def donut_chart(
    segments: list[dict], *, size: int = 200, stroke_width: int = 26, center_label: str = "", center_value: str = ""
) -> Markup:
    """segments: [{"label": str, "value": float, "color": css-color}, ...].
    A ring (not a filled pie) built from stacked stroked circles, with an
    optional label/value pair centered in the hole."""
    total = sum(s["value"] for s in segments)
    radius = (size - stroke_width) / 2
    circumference = 2 * math.pi * radius
    cx = cy = size / 2

    parts = [
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="Donut chart">',
        f'<circle cx="{cx}" cy="{cy}" r="{_fmt(radius)}" fill="none" stroke="var(--border)" '
        f'stroke-width="{stroke_width}"/>',
    ]
    if total > 0:
        offset = 0.0
        for seg in segments:
            if seg["value"] <= 0:
                continue
            length = (seg["value"] / total) * circumference
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{_fmt(radius)}" fill="none" stroke="{seg["color"]}" '
                f'stroke-width="{stroke_width}" stroke-dasharray="{_fmt(length)} {_fmt(circumference - length)}" '
                f'stroke-dashoffset="{_fmt(-offset)}" transform="rotate(-90 {cx} {cy})">'
                f'<title>{_esc(seg["label"])}: {_esc(seg["value"])}</title>'
                f"</circle>"
            )
            offset += length
    if center_value:
        parts.append(
            f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="22" font-weight="700" '
            f'fill="var(--text)">{_esc(center_value)}</text>'
        )
    if center_label:
        parts.append(
            f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" font-size="11" '
            f'fill="var(--text-muted)">{_esc(center_label)}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def area_chart(points: list[dict], *, width: int = 620, height: int = 220, color: str = "var(--brand)") -> Markup:
    """points: [{"label": str, "value": float}, ...], oldest first. A line
    with a soft fill underneath; x-axis labels are shown sparsely (first,
    middle, last) since a burndown window can carry 20+ points."""
    if not points:
        return Markup(f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"></svg>')

    pad_left, pad_right, pad_top, pad_bottom = 34, 12, 16, 24
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    values = [p["value"] for p in points]
    max_val = max(values + [0]) or 1
    n = len(points)
    step = plot_w / max(n - 1, 1)

    def xy(i: int, value: float) -> tuple[float, float]:
        x = pad_left + i * step
        y = pad_top + plot_h - (value / max_val) * plot_h
        return x, y

    coords = [xy(i, p["value"]) for i, p in enumerate(points)]
    line_path = "M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in coords)
    baseline_y = pad_top + plot_h
    area_path = line_path + f" L {_fmt(coords[-1][0])} {_fmt(baseline_y)} L {_fmt(coords[0][0])} {_fmt(baseline_y)} Z"

    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Line chart of story points remaining over time">',
    ]
    # y gridlines at 0%, 50%, 100% of max, with value labels.
    for frac in (0.0, 0.5, 1.0):
        y = pad_top + plot_h - frac * plot_h
        parts.append(f'<line x1="{pad_left}" y1="{_fmt(y)}" x2="{width - pad_right}" y2="{_fmt(y)}" stroke="var(--border)" stroke-width="1"/>')
        parts.append(f'<text x="{pad_left - 6}" y="{_fmt(y + 3)}" text-anchor="end" font-size="10" fill="var(--text-muted)">{_fmt(frac * max_val)}</text>')

    parts.append(f'<path d="{area_path}" fill="{color}" fill-opacity="0.12" stroke="none"/>')
    parts.append(f'<path d="{line_path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')

    label_indices = sorted({0, n // 2, n - 1})
    for i in label_indices:
        x, _ = coords[i]
        parts.append(
            f'<text x="{_fmt(x)}" y="{height - 6}" text-anchor="middle" font-size="10" '
            f'fill="var(--text-muted)">{_esc(points[i]["label"])}</text>'
        )
    last_x, last_y = coords[-1]
    parts.append(f'<circle cx="{_fmt(last_x)}" cy="{_fmt(last_y)}" r="3.5" fill="{color}"/>')

    parts.append("</svg>")
    return Markup("".join(parts))
