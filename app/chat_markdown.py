"""Turns the small markdown subset the L1 chat agent's replies actually use
(bold, bullet lists, paragraphs) into safe HTML, so the chat doesn't show
literal "**" and "* " to the reader. Not a general markdown parser -
narrow on purpose, matching what orchestrator/chat.py's system prompt
produces, rather than pulling in a markdown library for three formatting
rules.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BULLET_PREFIXES = ("* ", "- ")


def _inline_html(line: str) -> str:
    """Escape the line, then turn **bold** into <strong>. Escaping happens
    first so nothing in the text - model output, or in principle a stored
    message - can inject markup; only our own <strong>/<li>/<p>/<ul> tags,
    added after, are ever unescaped."""
    return _BOLD_RE.sub(r"<strong>\1</strong>", str(escape(line)))


def render_chat_markdown(text: str) -> Markup:
    """Converts one chat message's text to HTML. Blank-line-separated
    blocks become paragraphs (internal newlines become <br>), except a
    block where every line starts with "* " or "- ", which becomes a
    <ul>. Returns Markup so Jinja's autoescaping doesn't double-escape the
    HTML this function already built safely.
    """
    blocks = re.split(r"\n\s*\n", text.strip())
    html_blocks: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith(_BULLET_PREFIXES) for line in lines):
            items = "".join(f"<li>{_inline_html(line[2:])}</li>" for line in lines)
            html_blocks.append(f"<ul>{items}</ul>")
        else:
            html_blocks.append(f"<p>{'<br>'.join(_inline_html(line) for line in lines)}</p>")
    return Markup("".join(html_blocks))
