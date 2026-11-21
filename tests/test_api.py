from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_to_execution.api import create_app
from meeting_to_execution.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "meetings"


def meeting_payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        checkpoint_db=str(tmp_path / "checkpoints.db"),
        enable_scheduler=False,
        tracker_backend="mock",
        vector_backend="json",
        llm_api_key=None,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def approve(client: TestClient, thread_id: str, packet: dict, status: str = "approved"):
    review = {
        "packet_hash": packet["packet_hash"],
        "status": status,
        "decisions": [
            {
                "item_id": entry["item"]["id"],
                "action": "approve",
                "conflict_action": entry["proposed_conflict_action"],
            }
            for entry in packet["decisions"]
        ],
        "tasks": [
            {"task_id": task["id"], "action": "approve"} for task in packet["tasks"]
        ],
        "reviewer": "api-test",
    }
    return client.post(f"/api/reviews/{thread_id}", json=review)


def test_health_and_empty_state(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["tracker_backend"] == "mock"

    assert client.get("/api/meetings").json() == []
    assert client.get("/api/decisions").json() == []
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/stats").json()["meetings"] == 0


def test_ingest_review_and_approve_flow(client: TestClient) -> None:
    response = client.post("/api/meetings", json=meeting_payload("01_platform_planning.json"))
    assert response.status_code == 200
    body = response.json()
    thread_id = body["thread_id"]
    assert body["status"] == "awaiting_approval"
    packet = body["review"]
    assert len(packet["decisions"]) == 3
    assert len(packet["tasks"]) == 3

    pending = client.get(f"/api/reviews/{thread_id}")
    assert pending.status_code == 200
    assert pending.json()["packet_hash"] == packet["packet_hash"]

    # Nothing is persisted before human approval.
    assert client.get("/api/decisions").json() == []
    assert client.get("/api/tasks").json() == []

    approved = approve(client, thread_id, packet)
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["status"] == "approved"
    assert result["decisions_saved"] == 3
    assert result["tasks_posted"] == 3

    decisions = client.get("/api/decisions").json()
    assert len(decisions) == 3
    assert {d["status"] for d in decisions} == {"active"}

    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 3
    assert {task["status"] for task in tasks} == {"posted"}
    assert all(task["tracker_ref"] for task in tasks)

    stats = client.get("/api/stats").json()
    assert stats["meetings"] == 1
    assert stats["active_decisions"] == 3
    assert stats["tasks"] == 3
    assert stats["posted_tasks"] == 3

    reminders = client.get("/api/reminders").json()
    assert len(reminders) == 2
    assert {reminder["status"] for reminder in reminders} == {"scheduled"}


def test_duplicate_meeting_is_rejected(client: TestClient) -> None:
    payload = meeting_payload("01_platform_planning.json")
    assert client.post("/api/meetings", json=payload).status_code == 200
    duplicate = client.post("/api/meetings", json=payload)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_stale_packet_hash_returns_conflict(client: TestClient) -> None:
    body = client.post(
        "/api/meetings", json=meeting_payload("01_platform_planning.json")
    ).json()
    stale = approve(
        client,
        body["thread_id"],
        {**body["review"], "packet_hash": "stale"},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"]


def test_rejected_review_persists_nothing(client: TestClient) -> None:
    body = client.post(
        "/api/meetings", json=meeting_payload("02_platform_followup.json")
    ).json()
    response = approve(client, body["thread_id"], body["review"], status="rejected")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert client.get("/api/decisions").json() == []
    assert client.get("/api/tasks").json() == []


def test_unknown_review_and_meeting_return_404(client: TestClient) -> None:
    assert client.get("/api/reviews/missing").status_code == 404
    assert client.get("/api/meetings/missing").status_code == 404
    assert client.get("/api/decisions/missing").status_code == 404
    assert client.get("/api/tasks/missing").status_code == 404


def test_digest_job_and_notifications(client: TestClient) -> None:
    body = client.post(
        "/api/meetings", json=meeting_payload("01_platform_planning.json")
    ).json()
    assert approve(client, body["thread_id"], body["review"]).status_code == 200

    preview = client.get(
        "/api/digests/preview",
        params={"owner_id": "usr_priya", "week_start": "2026-11-16", "week_end": "2026-11-22"},
    )
    assert preview.status_code == 200
    assert "Priya" in preview.json()["text"]

    run = client.post(
        "/api/jobs/digest/run",
        params={"week_start": "2026-11-16", "week_end": "2026-11-22"},
    )
    assert run.status_code == 200
    digests = run.json()
    assert len(digests) == 2
    assert client.get("/api/digests").status_code == 200

    notifications = client.get("/api/notifications").json()
    assert len(notifications) >= 2
    assert {notification["kind"] for notification in notifications} == {"digest"}


def test_meeting_validation(client: TestClient) -> None:
    payload = meeting_payload("01_platform_planning.json")
    payload["transcript"] = ""
    assert client.post("/api/meetings", json=payload).status_code == 422
