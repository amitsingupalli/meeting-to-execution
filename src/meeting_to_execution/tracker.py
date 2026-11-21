from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .config import Settings
from .schemas import TaskDraft


class TrackerResult(BaseModel):
    ref: str
    provider: str
    created: bool = False
    raw: dict[str, Any] = {}


class Tracker(Protocol):
    provider: str

    def upsert_issue(self, task: TaskDraft) -> TrackerResult: ...


class MockTracker:
    provider = "mock"

    def __init__(self, path: str | Path = "./data/mock_tracker.json"):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {}

    def _save(self, issues: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(issues, indent=2, default=str))

    def upsert_issue(self, task: TaskDraft) -> TrackerResult:
        with self._lock:
            issues = self._load()
            existing = issues.get(task.idempotency_key)
            if existing:
                return TrackerResult(
                    ref=existing["url"],
                    provider=self.provider,
                    created=False,
                    raw=existing,
                )
            number = len(issues) + 1
            issue = {
                "number": number,
                "title": task.title,
                "body": task.body,
                "url": f"mock://tracker/issues/{number}",
                "owner_id": task.owner_id,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "idempotency_key": task.idempotency_key,
                "labels": ["meeting-action"],
            }
            issues[task.idempotency_key] = issue
            self._save(issues)
            return TrackerResult(
                ref=issue["url"],
                provider=self.provider,
                created=True,
                raw=issue,
            )


class GitHubTracker:
    provider = "github"

    def __init__(self, settings: Settings):
        if not settings.github_token or not settings.github_repo:
            raise ValueError("GITHUB_TOKEN and GITHUB_REPO are required for the GitHub tracker")
        self.token = settings.github_token
        self.repo = settings.github_repo
        self.api_url = settings.github_api_url.rstrip("/")
        self.client = httpx.Client(
            timeout=20,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    @staticmethod
    def _marker(idempotency_key: str) -> str:
        return f"<!-- mte-idempotency:{idempotency_key} -->"

    def _find_existing(self, task: TaskDraft) -> dict[str, Any] | None:
        marker = self._marker(task.idempotency_key)
        query = f'repo:{self.repo} is:issue "{task.idempotency_key}" in:body'
        response = self.client.get(
            f"{self.api_url}/search/issues",
            params={"q": query, "per_page": 10},
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            if marker in (item.get("body") or ""):
                return item
        return None

    def upsert_issue(self, task: TaskDraft) -> TrackerResult:
        existing = self._find_existing(task)
        if existing:
            return TrackerResult(
                ref=existing["html_url"],
                provider=self.provider,
                created=False,
                raw=existing,
            )

        owner = task.owner_id or "unassigned"
        deadline = task.deadline.isoformat() if task.deadline else "no deadline"
        body = (
            f"{task.body}\n\n"
            f"Owner: `{owner}`\n"
            f"Deadline: `{deadline}`\n\n"
            f"{self._marker(task.idempotency_key)}"
        )
        response = self.client.post(
            f"{self.api_url}/repos/{self.repo}/issues",
            json={
                "title": task.title,
                "body": body,
                "labels": ["meeting-action"],
            },
        )
        response.raise_for_status()
        issue = response.json()
        return TrackerResult(
            ref=issue["html_url"],
            provider=self.provider,
            created=True,
            raw=issue,
        )


def build_tracker(settings: Settings) -> Tracker:
    if settings.tracker_backend == "github":
        return GitHubTracker(settings)
    return MockTracker()
