"""Jira Cloud REST client.

Reads credentials from .env at the repo root. Nothing in this module prints
to stdout — stdio is the MCP transport, and a stray print() breaks it.
Errors are logged to stderr via the `logging` module instead.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

logger = logging.getLogger("gateway.jira")

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

API_VERSION = "3"


class JiraConfigError(RuntimeError):
    """Raised when required Jira configuration is missing from the environment."""


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    project_key: str
    story_points_field: str

    @classmethod
    def from_env(cls) -> "JiraConfig":
        base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        email = os.environ.get("JIRA_EMAIL", "")
        api_token = os.environ.get("JIRA_API_TOKEN", "")
        project_key = os.environ.get("JIRA_PROJECT_KEY", "")
        story_points_field = os.environ.get("JIRA_STORY_POINTS_FIELD", "")
        missing = [
            name
            for name, val in [
                ("JIRA_BASE_URL", base_url),
                ("JIRA_EMAIL", email),
                ("JIRA_API_TOKEN", api_token),
                ("JIRA_PROJECT_KEY", project_key),
            ]
            if not val
        ]
        if missing:
            raise JiraConfigError(f"Missing required .env keys: {', '.join(missing)}")
        return cls(base_url, email, api_token, project_key, story_points_field)


@dataclass
class Issue:
    key: str
    summary: str
    status: str
    issue_type: str
    story_points: float | None
    due_date: str | None
    labels: list[str]
    description: str | None = None


def _normalize_list(payload: Any) -> list[dict]:
    """Jira's own endpoints disagree on shape: /field and /screens/{id}/tabs
    return a bare JSON array, while /field/{id}/context and /search/jql
    return a paginated {"values": [...]} (or {"issues": [...]}) object.
    Normalise both to a plain list here so no call site has to special-case it.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "issues" in payload:
        return payload["issues"]
    if isinstance(payload, dict) and "values" in payload:
        return payload["values"]
    raise ValueError(f"Unrecognised Jira list payload shape: {type(payload)}")


def _adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format node tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    parts = [_adf_to_text(child) for child in node.get("content", []) or []]
    joined = "".join(parts)
    return joined + "\n" if node.get("type") in ("paragraph", "heading") else joined


def text_to_adf(text: str) -> dict:
    """Minimal plain-text -> ADF converter: one paragraph per line.

    Good enough for provenance comments; not a general Markdown->ADF converter.
    """
    lines = text.split("\n") or [""]
    content = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": line}] if line else [],
        }
        for line in lines
    ]
    return {"type": "doc", "version": 1, "content": content}


class JiraClient:
    """Thin wrapper over the Jira Cloud REST API v3, using Basic auth."""

    def __init__(self, config: JiraConfig | None = None):
        self.config = config or JiraConfig.from_env()
        self._client = httpx.Client(
            base_url=self.config.base_url,
            auth=(self.config.email, self.config.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, path, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error("Jira %s %s -> %s: %s", method, path, resp.status_code, resp.text[:500])
            raise
        return resp

    def myself(self) -> dict:
        return self._request("GET", f"/rest/api/{API_VERSION}/myself").json()

    def get_project(self, project_key: str | None = None) -> dict:
        key = project_key or self.config.project_key
        return self._request("GET", f"/rest/api/{API_VERSION}/project/{key}").json()

    def list_issue_types(self, project_key: str | None = None) -> list[dict]:
        """Issue types available for creation in a project (id, name, ...).

        This endpoint's payload shape (a paginated {"issueTypes": [...]})
        matches neither of the two cases _normalize_list handles, so it's
        unpacked here rather than folded into that helper.
        """
        key = project_key or self.config.project_key
        payload = self._request(
            "GET", f"/rest/api/{API_VERSION}/issue/createmeta/{key}/issuetypes"
        ).json()
        return payload.get("issueTypes", [])

    def create_issue(self, fields: dict) -> dict:
        """POST a new issue. Returns the created-issue payload (has 'key')."""
        return self._request("POST", f"/rest/api/{API_VERSION}/issue", json={"fields": fields}).json()

    def discover_field(self, name_contains: str) -> list[dict]:
        """List fields whose name contains the given substring (case-insensitive).

        Used once, by hand, to find a custom field ID such as story points —
        never call this on the hot path, the ID belongs in .env once found.
        """
        fields = _normalize_list(self._request("GET", f"/rest/api/{API_VERSION}/field").json())
        needle = name_contains.lower()
        return [f for f in fields if needle in f.get("name", "").lower()]

    def search_issues(self, jql: str, max_results: int = 50) -> list[Issue]:
        body = {
            "jql": jql,
            "maxResults": max_results,
            "fields": [
                "summary",
                "status",
                "issuetype",
                "duedate",
                "labels",
                self.config.story_points_field,
            ],
        }
        payload = self._request("POST", f"/rest/api/{API_VERSION}/search/jql", json=body).json()
        raw_issues = _normalize_list(payload)
        return [self._issue_from_raw(raw) for raw in raw_issues]

    def search_issues_for_analytics(self, jql: str, max_results: int = 200) -> list[dict]:
        """Raw per-issue rows for the dashboard (app/analytics.py): status
        category (todo/in-progress/done), resolution date, and creation
        date, on top of what search_issues() already reads. Kept separate
        from search_issues()/Issue - that dataclass and _issue_summary()'s
        dict shape in gateway/server.py are a fixed contract the MCP tool
        payloads and their tests depend on; analytics needs extra fields
        without touching it. maxResults defaults higher than
        search_issues() - a dashboard should see the whole project, not a
        50-issue page of it.
        """
        body = {
            "jql": jql,
            "maxResults": max_results,
            "fields": [
                "summary",
                "status",
                "issuetype",
                "duedate",
                "labels",
                "resolutiondate",
                "created",
                self.config.story_points_field,
            ],
        }
        payload = self._request("POST", f"/rest/api/{API_VERSION}/search/jql", json=body).json()
        raw_issues = _normalize_list(payload)
        rows = []
        for raw in raw_issues:
            fields = raw.get("fields", {})
            status = fields.get("status") or {}
            rows.append(
                {
                    "key": raw["key"],
                    "summary": fields.get("summary", ""),
                    "status": status.get("name", ""),
                    "status_category": (status.get("statusCategory") or {}).get("key", "new"),
                    "issue_type": (fields.get("issuetype") or {}).get("name", ""),
                    "story_points": fields.get(self.config.story_points_field),
                    "due_date": fields.get("duedate"),
                    "resolved_at": fields.get("resolutiondate"),
                    "created": fields.get("created"),
                    "labels": fields.get("labels", []) or [],
                }
            )
        return rows

    def get_issue(self, issue_key: str) -> Issue:
        raw = self._request(
            "GET",
            f"/rest/api/{API_VERSION}/issue/{issue_key}",
            params={
                "fields": ",".join(
                    [
                        "summary",
                        "status",
                        "issuetype",
                        "duedate",
                        "labels",
                        "description",
                        self.config.story_points_field,
                    ]
                )
            },
        ).json()
        return self._issue_from_raw(raw, include_description=True)

    def _issue_from_raw(self, raw: dict, include_description: bool = False) -> Issue:
        fields = raw.get("fields", {})
        return Issue(
            key=raw["key"],
            summary=fields.get("summary", ""),
            status=(fields.get("status") or {}).get("name", ""),
            issue_type=(fields.get("issuetype") or {}).get("name", ""),
            story_points=fields.get(self.config.story_points_field),
            due_date=fields.get("duedate"),
            labels=fields.get("labels", []) or [],
            description=_adf_to_text(fields.get("description")) if include_description else None,
        )

    def update_issue_fields(self, issue_key: str, fields: dict) -> None:
        """PUT arbitrary field updates to an issue.

        This client does not enforce guardrails — callers (the MCP tools in
        gateway/server.py) must run gateway.guardrails checks before calling this.
        """
        self._request("PUT", f"/rest/api/{API_VERSION}/issue/{issue_key}", json={"fields": fields})
