"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from db.migrate import apply_migrations
from gateway.jira import JiraClient


@pytest.fixture
def conn():
    """A fresh in-memory SQLite db, built by running the real
    db/migrations/*.sql through the exact same apply_migrations() path
    production uses - not a separate schema snapshot that could drift.

    check_same_thread=False for the same reason db/migrate.py's real
    connections use it: FastAPI's TestClient dispatches sync routes
    through a worker thread, not the thread that created the connection.
    """
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    apply_migrations(connection)
    yield connection
    connection.close()


@pytest.fixture
def jira_with_spy(monkeypatch):
    """A real JiraClient (reads real .env config) with its underlying
    httpx.Client swapped for a MagicMock, so a test can assert whether a
    Jira call actually happened rather than trusting the code path never
    reaches one. Construction makes no network call either way.
    """
    jira = JiraClient()
    spy = MagicMock()
    monkeypatch.setattr(jira, "_client", spy)
    return jira, spy
