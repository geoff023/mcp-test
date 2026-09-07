"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gateway.jira import JiraClient

SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


@pytest.fixture
def conn():
    """A fresh in-memory SQLite db built from the real schema - isolated
    from the on-disk demo db, but never a mock of the schema itself."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL)
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
