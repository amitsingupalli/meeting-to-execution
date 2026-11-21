from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from meeting_to_execution.config import Settings
from meeting_to_execution.db import Database
from meeting_to_execution.digest import DigestService
from meeting_to_execution.embeddings import HashEmbedding
from meeting_to_execution.extraction import RuleBasedExtractor
from meeting_to_execution.graph import MeetingPipeline
from meeting_to_execution.judge import RuleBasedJudge
from meeting_to_execution.llm import LLMClient
from meeting_to_execution.memory import MemoryStore
from meeting_to_execution.notifications import NotificationService
from meeting_to_execution.reminders import ReminderService
from meeting_to_execution.schemas import (
    DecisionReviewInput,
    MeetingCreate,
    ReviewPacket,
    ReviewSubmission,
    TaskReviewInput,
)
from meeting_to_execution.tracker import MockTracker
from meeting_to_execution.vector_index import JSONVectorIndex

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "meetings"


@dataclass
class Harness:
    memory: MemoryStore
    pipeline: MeetingPipeline
    tracker: MockTracker
    notifier: NotificationService
    reminders: ReminderService
    digests: DigestService
    data_dir: Path


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    db_path = tmp_path / "mte.db"
    db = Database(f"sqlite:///{db_path}")
    db.create_all()
    embeddings = HashEmbedding(384)
    vector = JSONVectorIndex(db_path, 384)
    memory = MemoryStore(db, vector, embeddings)
    notifier = NotificationService(memory, tmp_path / "notifications.log")
    reminders = ReminderService(memory, notifier, hours_before=48)
    digests = DigestService(memory, notifier, LLMClient(Settings(llm_api_key=None)))
    tracker = MockTracker(tmp_path / "tracker.json")
    pipeline = MeetingPipeline(
        memory=memory,
        extractor=RuleBasedExtractor(),
        judge=RuleBasedJudge(),
        tracker=tracker,
        reminders=reminders,
        checkpointer=InMemorySaver(),
    )
    return Harness(
        memory=memory,
        pipeline=pipeline,
        tracker=tracker,
        notifier=notifier,
        reminders=reminders,
        digests=digests,
        data_dir=tmp_path,
    )


def load_meeting(name: str) -> MeetingCreate:
    return MeetingCreate.model_validate(json.loads((FIXTURES / name).read_text()))


def build_review(
    packet: ReviewPacket,
    *,
    status: str = "approved",
    task_edits: dict | None = None,
) -> ReviewSubmission:
    task_edits = task_edits or {}
    decisions = [
        DecisionReviewInput(
            item_id=entry.item.id,
            action="approve",
            conflict_action=entry.proposed_conflict_action,
        )
        for entry in packet.decisions
    ]
    tasks = [
        TaskReviewInput(
            task_id=task.id,
            action="edit" if task.id in task_edits else "approve",
            edited=task_edits.get(task.id),
        )
        for task in packet.tasks
    ]
    return ReviewSubmission(
        packet_hash=packet.packet_hash,
        status=status,
        decisions=decisions,
        tasks=tasks,
        reviewer="test",
    )


def start_and_packet(
    harness: Harness, name: str, thread_id: str
) -> tuple[MeetingCreate, ReviewPacket]:
    meeting = load_meeting(name)
    harness.pipeline.start(meeting, thread_id)
    packet = harness.pipeline.get_packet(thread_id)
    assert packet is not None
    return meeting, packet
