from __future__ import annotations

from datetime import date

import pytest
from conftest import Harness, build_review, load_meeting, start_and_packet

from meeting_to_execution.schemas import EditableTask, ReviewSubmission


def test_ingest_stops_at_human_approval(harness: Harness) -> None:
    start_and_packet(harness, "01_platform_planning.json", "thread-gate")

    state = harness.pipeline.get_state("thread-gate")
    assert state.next == ("approval",)
    assert harness.memory.list_decisions() == []
    assert harness.memory.list_tasks() == []
    assert harness.memory.get_meeting("mtg_001_platform_planning").status == "processing"


def test_rejection_writes_nothing(harness: Harness) -> None:
    _, packet = start_and_packet(harness, "01_platform_planning.json", "thread-reject")
    result = harness.pipeline.resume(
        "thread-reject", build_review(packet, status="rejected")
    )

    assert result["rejected"] is True
    assert harness.memory.list_decisions() == []
    assert harness.memory.list_tasks() == []
    assert harness.memory.get_meeting("mtg_001_platform_planning").status == "rejected"


def test_stale_packet_hash_is_rejected(harness: Harness) -> None:
    _, packet = start_and_packet(harness, "01_platform_planning.json", "thread-stale")
    review = build_review(packet).model_copy(update={"packet_hash": "not-the-hash"})

    with pytest.raises(ValueError, match="stale approval packet"):
        harness.pipeline.resume("thread-stale", review)
    assert harness.memory.list_decisions() == []


def test_approval_requires_a_verdict_for_every_item(harness: Harness) -> None:
    _, packet = start_and_packet(harness, "01_platform_planning.json", "thread-partial")
    review = build_review(packet)
    review = review.model_copy(update={"decisions": review.decisions[:-1]})

    with pytest.raises(ValueError, match="review missing decisions"):
        harness.pipeline.resume("thread-partial", review)
    assert harness.memory.list_decisions() == []


def test_approval_commits_posts_and_schedules(harness: Harness) -> None:
    _, packet = start_and_packet(harness, "01_platform_planning.json", "thread-approve")
    result = harness.pipeline.resume("thread-approve", build_review(packet))

    assert len(result["approved_decisions"]) == 3
    assert len(result["posted"]) == 3
    assert harness.memory.get_meeting("mtg_001_platform_planning").status == "approved"

    tasks = harness.memory.list_tasks()
    assert {task.status for task in tasks} == {"posted"}
    assert all(task.tracker_ref and task.tracker_ref.startswith("mock://") for task in tasks)
    assert len(harness.memory.list_reminders()) == 2

    stored = {d.subject: d for d in harness.memory.list_decisions()}
    assert stored["database"].value == "Postgres"
    assert stored["database"].status == "active"


def test_reviewer_can_assign_an_unowned_task(harness: Harness) -> None:
    _, packet = start_and_packet(harness, "01_platform_planning.json", "thread-edit")
    auth = next(task for task in packet.tasks if "authentication" in task.title)
    edit = EditableTask(
        title=auth.title,
        body=auth.body,
        owner_id="usr_elena",
        deadline=date(2026, 11, 20),
    )

    harness.pipeline.resume("thread-edit", build_review(packet, task_edits={auth.id: edit}))

    saved = next(
        task for task in harness.memory.list_tasks() if "authentication" in task.title
    )
    assert saved.owner_id == "usr_elena"
    assert saved.deadline == date(2026, 11, 20)


def test_second_meeting_supersedes_conflicting_decisions(harness: Harness) -> None:
    _, first = start_and_packet(harness, "01_platform_planning.json", "thread-m1")
    harness.pipeline.resume("thread-m1", build_review(first))

    _, third = start_and_packet(harness, "03_architecture_review.json", "thread-m3")
    database = next(d for d in third.decisions if d.item.subject == "database")
    assert database.proposed_conflict_action == "supersede"
    harness.pipeline.resume("thread-m3", build_review(third))

    decisions = harness.memory.list_decisions()
    active = {
        (d.subject, d.attribute): d for d in decisions if d.status == "active"
    }
    assert active[("database", "primary database")].value == "MySQL"
    assert active[("beta launch", "launch date")].value == "December 18"

    superseded = [d for d in decisions if d.status == "superseded"]
    assert {d.value for d in superseded} == {"Postgres", "December 4"}

    relations = {c.relation for c in harness.memory.list_conflicts()}
    assert "contradicts" in relations
    assert "refines" in relations


def test_duplicate_decision_is_not_written_twice(harness: Harness) -> None:
    _, first = start_and_packet(harness, "01_platform_planning.json", "thread-dup-1")
    harness.pipeline.resume("thread-dup-1", build_review(first))

    _, second = start_and_packet(harness, "02_platform_followup.json", "thread-dup-2")
    launch = next(d for d in second.decisions if d.item.subject == "beta launch")
    assert launch.proposed_conflict_action == "duplicate"
    harness.pipeline.resume("thread-dup-2", build_review(second))

    launches = [
        d
        for d in harness.memory.list_decisions()
        if d.subject == "beta launch" and d.status == "active"
    ]
    assert len(launches) == 1
    assert launches[0].value == "December 4"


def test_duplicate_action_item_is_skipped(harness: Harness) -> None:
    _, first = start_and_packet(harness, "01_platform_planning.json", "thread-task-1")
    harness.pipeline.resume("thread-task-1", build_review(first))

    _, second = start_and_packet(harness, "02_platform_followup.json", "thread-task-2")
    duplicate = next(task for task in second.tasks if task.title == "draft the schema")
    assert duplicate.status == "skipped"
    assert duplicate.duplicate_of

    result = harness.pipeline.resume("thread-task-2", build_review(second))
    assert len(result["posted"]) == 1
    assert len([t for t in harness.memory.list_tasks() if t.title == "draft the schema"]) == 1


def test_tracker_posting_is_idempotent(harness: Harness) -> None:
    from meeting_to_execution.extraction import RuleBasedExtractor
    from meeting_to_execution.normalization import build_task_draft, normalize_items

    meeting = load_meeting("01_platform_planning.json")
    items = normalize_items(meeting, RuleBasedExtractor().extract(meeting))
    action = next(item for item in items if item.type == "action_item")
    draft = build_task_draft(action, meeting)

    first = harness.tracker.upsert_issue(draft)
    second = harness.tracker.upsert_issue(draft)

    assert first.created is True
    assert second.created is False
    assert first.ref == second.ref
    assert len(harness.tracker._load()) == 1


def test_review_submission_requires_edit_payload() -> None:
    from pydantic import ValidationError

    from meeting_to_execution.schemas import DecisionReviewInput

    with pytest.raises(ValidationError):
        DecisionReviewInput(item_id="x", action="edit")

    assert isinstance(
        ReviewSubmission(
            packet_hash="h",
            status="rejected",
            note="not this week",
        ),
        ReviewSubmission,
    )
