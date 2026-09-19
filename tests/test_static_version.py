"""Regression test for the stylesheet cache-busting fix: StaticFiles sets
no explicit Cache-Control, so browsers can keep serving a stale
/static/style.css indefinitely even across a normal reload - confirmed
directly this session. base.html appends app.main._static_version()
(the file's mtime) to the stylesheet URL so a normal page load always
gets the current CSS."""

from __future__ import annotations

import re
import sqlite3

import pytest
from starlette.testclient import TestClient

import app.main as app_main


@pytest.fixture
def client(conn, jira_with_spy, monkeypatch):
    jira, _ = jira_with_spy
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(app_main, "_conn", conn)
    monkeypatch.setattr(app_main, "_jira", jira)
    return TestClient(app_main.app)


def test_stylesheet_link_carries_a_cache_busting_version(client):
    resp = client.get("/")

    match = re.search(r'href="/static/style\.css\?v=(\d+)"', resp.text)
    assert match is not None


def test_static_version_changes_when_the_file_mtime_changes(monkeypatch):
    class _FakePath:
        def __init__(self, mtime):
            self._mtime = mtime

        def stat(self):
            return type("S", (), {"st_mtime": self._mtime})()

    monkeypatch.setattr(app_main, "_STYLE_CSS_PATH", _FakePath(1000))
    v1 = app_main._static_version()
    monkeypatch.setattr(app_main, "_STYLE_CSS_PATH", _FakePath(2000))
    v2 = app_main._static_version()

    assert v1 != v2
