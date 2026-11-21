from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .container import Container
from .schemas import (
    DecisionReviewInput,
    EditableTask,
    ReviewPacket,
    ReviewSubmission,
    TaskReviewInput,
)


def _fixtures() -> list[Path]:
    return sorted(Path("fixtures/meetings").glob("*.json"))


def _load(path: Path):
    from .schemas import MeetingCreate

    return MeetingCreate.model_validate(json.loads(path.read_text()))


def _review(
    packet: ReviewPacket,
    *,
    task_edits: dict[str, EditableTask] | None = None,
) -> ReviewSubmission:
    task_edits = task_edits or {}
    decisions = [
        DecisionReviewInput(
            item_id=item.item.id,
            action="approve",
            conflict_action=item.proposed_conflict_action,
        )
        for item in packet.decisions
    ]
    tasks = []
    for task in packet.tasks:
        edited = task_edits.get(task.id)
        if edited:
            tasks.append(
                TaskReviewInput(task_id=task.id, action="edit", edited=edited)
            )
        else:
            tasks.append(TaskReviewInput(task_id=task.id, action="approve"))
    return ReviewSubmission(
        packet_hash=packet.packet_hash,
        status="approved",
        decisions=decisions,
        tasks=tasks,
        reviewer="demo",
    )


def _print_packet(packet: ReviewPacket) -> None:
    print(f"\nReview packet: {packet.meeting.title} ({packet.meeting.meeting_date})")
    print("Decisions:")
    for entry in packet.decisions:
        item = entry.item
        print(
            f"  - {item.subject} / {item.attribute or 'general'} = {item.value or '(none)'}"
            f"  [proposed: {entry.proposed_conflict_action}]"
        )
        for conflict in entry.conflicts:
            old = conflict.old_decision
            print(
                f"      {conflict.verdict.relation.upper()}: "
                f"{old.value!r} -> {item.value!r}"
            )
            print(f"      reason: {conflict.verdict.reasoning}")
    print("Tasks:")
    for task in packet.tasks:
        print(
            f"  - {task.title} | owner={task.owner_name or 'UNASSIGNED'} | "
            f"due={task.deadline or 'none'} | status={task.status}"
        )
    for warning in packet.warnings:
        print(f"  ! {warning}")


def _approve(container: Container, thread_id: str, packet: ReviewPacket, **kwargs) -> dict:
    result = container.pipeline.resume(thread_id, _review(packet, **kwargs))
    print(
        f"  -> saved {len(result.get('approved_decisions', []))} decision(s), "
        f"posted {len(result.get('posted', []))} task(s), "
        f"superseded {len(result.get('superseded', []))} decision(s)"
    )
    return result


def run_demo() -> None:
    data_dir = Path("data/demo")
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("demo.db", "demo_checkpoints.db", "mock_tracker.json"):
        (data_dir / name).unlink(missing_ok=True)

    settings = Settings(
        app_env="demo",
        database_url=f"sqlite:///{(data_dir / 'demo.db').resolve()}",
        checkpoint_db=str((data_dir / "demo_checkpoints.db").resolve()),
        enable_scheduler=False,
        tracker_backend="mock",
        vector_backend="local",
    )
    container = Container.build(settings)
    try:
        print("=" * 78)
        print("MEETING TO EXECUTION - END-TO-END DEMO")
        print("=" * 78)

        fixtures = _fixtures()
        if len(fixtures) < 4:
            raise SystemExit("Expected 4 meeting fixtures in fixtures/meetings")

        for index, path in enumerate(fixtures, start=1):
            meeting = _load(path)
            thread_id = f"demo-{index}"
            container.pipeline.start(meeting, thread_id)
            packet = container.pipeline.get_packet(thread_id)
            if packet is None:
                raise SystemExit(f"No review packet for {path.name}")
            _print_packet(packet)

            task_edits: dict[str, EditableTask] = {}
            if index == 1:
                auth_task = next(
                    (t for t in packet.tasks if "authentication" in t.title.lower()), None
                )
                if auth_task:
                    task_edits[auth_task.id] = EditableTask(
                        title=auth_task.title,
                        body=auth_task.body + "\nEdited by reviewer: assign to Elena.",
                        owner_id="usr_elena",
                        deadline=auth_task.deadline,
                    )
                    print(
                        "  Reviewer edit: assign the authentication investigation to Elena"
                    )
            _approve(container, thread_id, packet, task_edits=task_edits)

        print("\n" + "=" * 78)
        print("DECISION MEMORY")
        print("=" * 78)
        for decision in container.memory.list_decisions():
            print(
                f"  [{decision.status:10}] {decision.subject} / "
                f"{decision.attribute or 'general'} = {decision.value}"
            )

        print("\n" + "=" * 78)
        print("TRACKED TASKS")
        print("=" * 78)
        for task in container.memory.list_tasks():
            print(
                f"  [{task.status:8}] {task.title} | {task.owner_name or 'UNASSIGNED'} "
                f"| due {task.deadline or 'none'} | {task.tracker_ref or 'not posted'}"
            )

        print("\n" + "=" * 78)
        print("REMINDERS")
        print("=" * 78)
        future = datetime.now(UTC) + timedelta(days=2)
        sent = container.reminders.run_due(now=future)
        for item in sent:
            print(f"  sent reminder for task {item['task_id']} to {item['owner_id']}")

        print("\n" + "=" * 78)
        print("WEEKLY DIGESTS")
        print("=" * 78)
        for digest in container.digests.run_weekly():
            print(f"\n  To {digest.owner_name or digest.owner_id}:")
            for line in digest.text.splitlines():
                print(f"    {line}")

        print("\n" + "=" * 78)
        print("NOTIFICATIONS")
        print("=" * 78)
        for notification in container.memory.list_notifications(limit=20):
            print(
                f"  [{notification.kind}] -> {notification.recipient}: "
                f"{notification.subject}"
            )

        stats = container.memory.stats()
        print("\n" + "=" * 78)
        print(
            f"SUMMARY: {stats.meetings} meetings | {stats.active_decisions} active decisions | "
            f"{stats.superseded_decisions} superseded | {stats.tasks} tasks | "
            f"{stats.posted_tasks} posted"
        )
        print("=" * 78)
    finally:
        container.close()
