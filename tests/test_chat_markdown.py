"""Tests for app/chat_markdown.py - the narrow bold/bullet/paragraph
renderer for L1 chat replies (see app/templates/chat.html)."""

from __future__ import annotations

from app.chat_markdown import render_chat_markdown


def test_renders_bold_inside_a_paragraph():
    html = render_chat_markdown("The story is **MCP-2**.")
    assert html == "<p>The story is <strong>MCP-2</strong>.</p>"


def test_renders_a_bullet_block_as_a_list():
    text = "* **MCP-2**: Story-points probe\n* **MCP-4**: Add OAuth login flow"
    html = render_chat_markdown(text)
    assert html == (
        "<ul><li><strong>MCP-2</strong>: Story-points probe</li>"
        "<li><strong>MCP-4</strong>: Add OAuth login flow</li></ul>"
    )


def test_renders_intro_paragraph_then_bullet_list_as_separate_blocks():
    text = "Here are the stories:\n\n* MCP-1\n* MCP-2"
    html = render_chat_markdown(text)
    assert html == "<p>Here are the stories:</p><ul><li>MCP-1</li><li>MCP-2</li></ul>"


def test_plain_text_with_no_markdown_passes_through_as_a_paragraph():
    html = render_chat_markdown("There are no unestimated stories right now.")
    assert html == "<p>There are no unestimated stories right now.</p>"


def test_internal_newlines_within_a_paragraph_become_br():
    html = render_chat_markdown("Line one\nLine two")
    assert html == "<p>Line one<br>Line two</p>"


def test_escapes_html_so_model_output_cannot_inject_markup():
    html = render_chat_markdown("<script>alert(1)</script> & **bold**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    assert "<strong>bold</strong>" in html


def test_a_single_asterisk_is_not_treated_as_a_bullet_or_bold():
    html = render_chat_markdown("5 * 3 = 15")
    assert html == "<p>5 * 3 = 15</p>"
