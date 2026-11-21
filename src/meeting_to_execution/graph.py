from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .config import Settings
from .extraction import HybridExtractor
from .judge import HybridJudge
from .memory import MemoryStore
from .normalization import build_task_draft, normalize_items, stable_id
from .reminders import ReminderService
from .schemas import (
    CandidateView,
    ConflictView,
    DecisionRecord,
    DecisionView,
    MeetingCreate,
    NormalizedItem,
    ReviewConflictView,
    ReviewDecisionView,
    ReviewPacket,
    ReviewSubmission,
    TaskDraft,
    TaskView,
)
from .tracker import Tracker


class GraphState(TypedDict, total=False):
    thread_id: str
    meeting: MeetingCreate
    items: list[NormalizedItem]
    decisions: list[NormalizedItem]
    tasks: list[TaskDraft]
    candidates: dict[str, list[CandidateView]]
    conflicts: list[ConflictView]
    packet: ReviewPacket
    packet_hash: str
    review: ReviewSubmission
    approved_decisions: list[DecisionRecord]
    approved_tasks: list[TaskDraft]
    posted: list[TaskView]
    reminders: list[dict]
    superseded: list[str]
    duplicate_of: dict[str, str]
    rejected: bool


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _task_view(
    draft: TaskDraft, meeting: MeetingCreate, *, owner_name: str | None = None
) -> TaskView:
    return TaskView(
        id=draft.id,
        meeting_id=draft.meeting_id,
        item_id=draft.item_id,
        title=draft.title,
        body=draft.body,
        owner_id=draft.owner_id,
        owner_name=owner_name,
        deadline=draft.deadline,
        status=draft.status,
        dedupe_key=draft.dedupe_key,
        idempotency_key=draft.idempotency_key,
        duplicate_of=draft.duplicate_of,
        created_at=datetime.now(UTC),
    )


class MeetingPipeline:
    def __init__(
        self,
        *,
        memory: MemoryStore,
        extractor: HybridExtractor,
        judge: HybridJudge,
        tracker: Tracker,
        reminders: ReminderService,
        checkpointer: Any,
    ):
        self.memory = memory
        self.extractor = extractor
        self.judge = judge
        self.tracker = tracker
        self.reminders = reminders
        self.checkpointer = checkpointer
        self._lock = threading.RLock()
        self.graph = self._build()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _ingest(self, state: GraphState) -> dict[str, Any]:
        meeting = state["meeting"]
        meeting_id = meeting.meeting_id or (
            f"mtg_{stable_id(meeting.title, meeting.meeting_date.isoformat())}"
        )
        if meeting_id != meeting.meeting_id:
            meeting = meeting.model_copy(update={"meeting_id": meeting_id})
        existing = self.memory.get_meeting(meeting_id)
        if not existing:
            self.memory.save_meeting(meeting, meeting_id)
        return {"meeting": meeting}

    def _extract(self, state: GraphState) -> dict[str, Any]:
        return {"items": self.extractor.extract(state["meeting"])}

    def _normalize(self, state: GraphState) -> dict[str, Any]:
        meeting = state["meeting"]
        items = normalize_items(meeting, state["items"])
        decisions = [item for item in items if item.type == "decision"]
        tasks = [build_task_draft(item, meeting) for item in items if item.type == "action_item"]
        return {"items": items, "decisions": decisions, "tasks": tasks}

    def _retrieve(self, state: GraphState) -> dict[str, Any]:
        meeting_id = state["meeting"].meeting_id
        return {
            "candidates": {
                decision.id: self.memory.candidates(
                    decision, meeting_id=meeting_id, limit=8
                )
                for decision in state["decisions"]
            }
        }

    def _judge(self, state: GraphState) -> dict[str, Any]:
        conflicts: list[ConflictView] = []
        for decision in state["decisions"]:
            for candidate in state["candidates"].get(decision.id, []):
                conflicts.append(self.judge.judge(decision, candidate.decision))
        return {"conflicts": conflicts}

    def _prepare_review(self, state: GraphState) -> dict[str, Any]:
        meeting = state["meeting"]
        meeting_view = self.memory.get_meeting(meeting.meeting_id or "")
        if meeting_view is None:
            raise RuntimeError("meeting was not persisted")

        old_by_id: dict[str, DecisionView] = {
            candidate.decision.id: candidate.decision
            for candidates in state["candidates"].values()
            for candidate in candidates
        }
        review_decisions: list[ReviewDecisionView] = []
        for decision in state["decisions"]:
            relevant = [
                conflict
                for conflict in state["conflicts"]
                if conflict.new_item_id == decision.id and conflict.relation != "unrelated"
            ]
            if any(c.relation in ("contradicts", "refines") for c in relevant):
                proposed = "supersede"
            elif any(c.relation == "duplicates" for c in relevant):
                proposed = "duplicate"
            else:
                proposed = "none"
            duplicate_of = next(
                (c.old_decision_id for c in relevant if c.relation == "duplicates"), None
            )
            review_decisions.append(
                ReviewDecisionView(
                    item=decision,
                    conflicts=[
                        ReviewConflictView(
                            verdict=conflict,
                            old_decision=old_by_id[conflict.old_decision_id],
                        )
                        for conflict in relevant
                        if conflict.old_decision_id in old_by_id
                    ],
                    proposed_conflict_action=proposed,
                    proposed_duplicate_of=duplicate_of,
                )
            )

        owner_names = {a.id: a.name for a in meeting.attendees}
        tasks: list[TaskView] = []
        for draft in state["tasks"]:
            duplicate = self.memory.find_task_duplicate(draft, exclude_id=draft.id)
            if duplicate:
                draft = draft.model_copy(
                    update={"status": "skipped", "duplicate_of": duplicate.id}
                )
            tasks.append(
                _task_view(draft, meeting, owner_name=owner_names.get(draft.owner_id or ""))
            )

        warnings: list[str] = []
        unresolved_owners = sum(
            1
            for item in state["decisions"]
            if "owner_unresolved" in item.flags or "owner_missing" in item.flags
        )
        unresolved_deadlines = sum(
            1 for item in state["items"] if "deadline_unresolved" in item.flags
        )
        missing_quotes = sum(
            1 for item in state["items"] if "source_quote_not_found" in item.flags
        )
        if unresolved_owners:
            warnings.append(f"{unresolved_owners} item(s) have unresolved owners")
        if unresolved_deadlines:
            warnings.append(f"{unresolved_deadlines} item(s) have unresolved deadlines")
        if missing_quotes:
            warnings.append(f"{missing_quotes} item(s) have source quotes not found verbatim")
        for task in tasks:
            if task.duplicate_of:
                warnings.append(
                    f"Task '{task.title}' is a duplicate of existing task {task.duplicate_of}"
                )

        generated_at = datetime.now(UTC)
        packet = ReviewPacket(
            thread_id=state["thread_id"],
            meeting=meeting_view,
            decisions=review_decisions,
            tasks=tasks,
            warnings=warnings,
            packet_hash="",
            generated_at=generated_at,
        )
        packet_hash = _sha(
            _canonical(packet.model_dump(mode="json", exclude={"packet_hash"}))
        )
        packet = packet.model_copy(update={"packet_hash": packet_hash})
        self.memory.save_pending_review(
            thread_id=state["thread_id"],
            meeting_id=meeting.meeting_id or "",
            packet_hash=packet_hash,
        )
        return {
            "packet": packet,
            "packet_hash": packet_hash,
            "tasks": [
                TaskDraft(
                    id=task.id,
                    meeting_id=task.meeting_id,
                    item_id=task.item_id,
                    title=task.title,
                    body=task.body,
                    owner_id=task.owner_id,
                    deadline=task.deadline,
                    status=task.status,
                    dedupe_key=task.dedupe_key,
                    idempotency_key=task.idempotency_key,
                    duplicate_of=task.duplicate_of,
                )
                for task in tasks
            ],
        }

    def _approval(self, state: GraphState) -> dict[str, Any]:
        packet = state["packet"]
        payload = interrupt(packet.model_dump(mode="json"))
        review = (
            payload
            if isinstance(payload, ReviewSubmission)
            else ReviewSubmission.model_validate(payload)
        )
        if review.packet_hash != state["packet_hash"]:
            raise ValueError("stale approval packet: packet_hash does not match")

        if review.status == "approved":
            all_decision_ids = {item.id for item in state["decisions"]}
            all_task_ids = {task.id for task in state["tasks"]}
            required_decisions = all_decision_ids
            required_tasks = {
                task.id for task in state["tasks"] if task.status != "skipped"
            }
            provided_decisions = {item.item_id for item in review.decisions}
            provided_tasks = {item.task_id for item in review.tasks}

            if unknown := provided_decisions - all_decision_ids:
                raise ValueError(f"unknown decision ids in review: {sorted(unknown)}")
            if unknown := provided_tasks - all_task_ids:
                raise ValueError(f"unknown task ids in review: {sorted(unknown)}")
            if missing := required_decisions - provided_decisions:
                raise ValueError(f"review missing decisions: {sorted(missing)}")
            if missing := required_tasks - provided_tasks:
                raise ValueError(f"review missing tasks: {sorted(missing)}")

            valid_owners = {attendee.id for attendee in state["meeting"].attendees}
            for item in review.decisions:
                if item.action == "edit" and item.edited:
                    if item.edited.owner_id and item.edited.owner_id not in valid_owners:
                        raise ValueError(f"unknown owner: {item.edited.owner_id}")
            for item in review.tasks:
                if item.action == "edit" and item.edited:
                    if item.edited.owner_id and item.edited.owner_id not in valid_owners:
                        raise ValueError(f"unknown owner: {item.edited.owner_id}")

        return {"review": review}

    def _commit(self, state: GraphState) -> dict[str, Any]:
        review = state["review"]
        if review.status != "approved" or review.packet_hash != state["packet_hash"]:
            raise RuntimeError("approval gate not satisfied")

        meeting = state["meeting"]
        decisions_by_id = {item.item_id: item for item in review.decisions}
        tasks_by_id = {item.task_id: item for item in review.tasks}
        valid_owners = {attendee.id for attendee in meeting.attendees}
        now = datetime.now(UTC)
        superseded: set[str] = set()
        duplicate_of: dict[str, str] = {}
        approved_decisions: list[DecisionRecord] = []
        approved_task_drafts: list[TaskDraft] = []

        for decision in state["decisions"]:
            item = decisions_by_id.get(decision.id)
            if not item or item.action == "reject":
                continue
            if item.conflict_action == "duplicate":
                old_id = next(
                    (
                        conflict.old_decision_id
                        for conflict in state["conflicts"]
                        if conflict.new_item_id == decision.id
                        and conflict.relation == "duplicates"
                    ),
                    None,
                )
                if old_id:
                    duplicate_of[decision.id] = old_id
                continue

            updated = decision
            if item.action == "edit" and item.edited:
                if item.edited.owner_id and item.edited.owner_id not in valid_owners:
                    raise ValueError(f"unknown owner: {item.edited.owner_id}")
                updated = decision.model_copy(
                    update={
                        "subject": item.edited.subject,
                        "attribute": item.edited.attribute,
                        "value": item.edited.value,
                        "owner": item.edited.owner_id,
                        "owner_text": item.edited.owner_id or decision.owner_text,
                        "deadline": item.edited.deadline,
                        "confidence": item.edited.confidence,
                    }
                )

            final = DecisionRecord(
                **updated.model_dump(),
                status="active",
                meeting_id=meeting.meeting_id or "",
                meeting_date=meeting.meeting_date,
                created_at=now,
                supersedes=[],
            )
            if item.conflict_action == "supersede":
                for conflict in state["conflicts"]:
                    if conflict.new_item_id == decision.id and conflict.relation in (
                        "contradicts",
                        "refines",
                    ):
                        final.supersedes.append(conflict.old_decision_id)
                        superseded.add(conflict.old_decision_id)
            approved_decisions.append(final)

        for draft in state["tasks"]:
            item = tasks_by_id.get(draft.id)
            if not item or item.action == "reject" or draft.status == "skipped":
                continue
            updated = draft
            if item.action == "edit" and item.edited:
                if item.edited.owner_id and item.edited.owner_id not in valid_owners:
                    raise ValueError(f"unknown owner: {item.edited.owner_id}")
                updated = draft.model_copy(
                    update={
                        "title": item.edited.title,
                        "body": item.edited.body or draft.body,
                        "owner_id": item.edited.owner_id,
                        "deadline": item.edited.deadline,
                        "status": "approved",
                    }
                )
            else:
                updated = draft.model_copy(update={"status": "approved"})
            approved_task_drafts.append(updated)

        approved_ids = {decision.id for decision in approved_decisions}
        saved_tasks = self.memory.commit_plan(
            thread_id=state["thread_id"],
            meeting_id=meeting.meeting_id or "",
            decisions=approved_decisions,
            tasks=approved_task_drafts,
            superseded=superseded,
            duplicate_of=duplicate_of,
            conflicts=[
                conflict
                for conflict in state["conflicts"]
                if conflict.new_item_id in approved_ids
            ],
            review=review,
        )
        return {
            "approved_decisions": approved_decisions,
            "approved_tasks": saved_tasks,
            "superseded": sorted(superseded),
            "duplicate_of": duplicate_of,
        }

    def _post(self, state: GraphState) -> dict[str, Any]:
        review = state.get("review")
        if (
            not review
            or review.status != "approved"
            or review.packet_hash != state["packet_hash"]
        ):
            raise RuntimeError("approval gate not satisfied")

        posted: list[TaskView] = []
        updated_tasks: list[TaskDraft] = []
        for task in state.get("approved_tasks", []):
            if task.status != "approved":
                updated_tasks.append(task)
                continue
            duplicate = self.memory.find_task_duplicate(task, exclude_id=task.id)
            if duplicate and duplicate.status == "posted":
                task = task.model_copy(
                    update={"status": "skipped", "duplicate_of": duplicate.id}
                )
                self.memory.save_task(task)
                updated_tasks.append(task)
                continue
            result = self.tracker.upsert_issue(task)
            view = self.memory.update_task_posted(task.id, result.ref, result.provider)
            if view:
                posted.append(view)
                updated_tasks.append(
                    task.model_copy(
                        update={
                            "status": "posted",
                            "duplicate_of": view.duplicate_of,
                        }
                    )
                )
            else:
                updated_tasks.append(task)
        return {"posted": posted, "approved_tasks": updated_tasks}

    def _schedule(self, state: GraphState) -> dict[str, Any]:
        tasks = [
            task for task in state.get("approved_tasks", []) if task.status == "posted"
        ]
        reminders = self.reminders.schedule_tasks(tasks)
        return {"reminders": [reminder.model_dump(mode="json") for reminder in reminders]}

    def _reject(self, state: GraphState) -> dict[str, Any]:
        self.memory.reject_review(
            thread_id=state["thread_id"],
            meeting_id=state["meeting"].meeting_id or "",
            review=state["review"],
        )
        return {"rejected": True}

    def _route_approval(self, state: GraphState) -> str:
        return "approved" if state["review"].status == "approved" else "rejected"

    def _build(self):
        graph = StateGraph(GraphState)
        graph.add_node("ingest", self._ingest)
        graph.add_node("extract", self._extract)
        graph.add_node("normalize", self._normalize)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("judge", self._judge)
        graph.add_node("prepare_review", self._prepare_review)
        graph.add_node("approval", self._approval)
        graph.add_node("commit", self._commit)
        graph.add_node("post", self._post)
        graph.add_node("schedule", self._schedule)
        graph.add_node("reject", self._reject)

        graph.add_edge(START, "ingest")
        graph.add_edge("ingest", "extract")
        graph.add_edge("extract", "normalize")
        graph.add_edge("normalize", "retrieve")
        graph.add_edge("retrieve", "judge")
        graph.add_edge("judge", "prepare_review")
        graph.add_edge("prepare_review", "approval")
        graph.add_conditional_edges(
            "approval",
            self._route_approval,
            {"approved": "commit", "rejected": "reject"},
        )
        graph.add_edge("commit", "post")
        graph.add_edge("post", "schedule")
        graph.add_edge("schedule", END)
        graph.add_edge("reject", END)
        return graph.compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @staticmethod
    def config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, meeting: MeetingCreate, thread_id: str) -> dict[str, Any]:
        with self._lock:
            return self.graph.invoke(
                {"thread_id": thread_id, "meeting": meeting},
                self.config(thread_id),
            )

    def resume(self, thread_id: str, review: ReviewSubmission) -> dict[str, Any]:
        with self._lock:
            return self.graph.invoke(
                Command(resume=review.model_dump(mode="json")),
                self.config(thread_id),
            )

    def get_state(self, thread_id: str):
        with self._lock:
            return self.graph.get_state(self.config(thread_id))

    def get_packet(self, thread_id: str) -> ReviewPacket | None:
        state = self.get_state(thread_id)
        packet = state.values.get("packet") if state and state.values else None
        return packet if isinstance(packet, ReviewPacket) else None


def build_checkpointer(path: str | Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, check_same_thread=False)
    allowed = [
        ("meeting_to_execution.schemas", name)
        for name in (
            "MeetingCreate",
            "MeetingView",
            "NormalizedItem",
            "DecisionRecord",
            "DecisionView",
            "TaskDraft",
            "TaskView",
            "CandidateView",
            "ConflictView",
            "ReviewPacket",
            "ReviewDecisionView",
            "ReviewConflictView",
            "ReviewSubmission",
        )
    ]
    serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
    saver = SqliteSaver(connection, serde=serde)
    saver.setup()
    return saver, connection


def build_pipeline(
    *,
    settings: Settings,
    memory: MemoryStore,
    extractor: HybridExtractor,
    judge: HybridJudge,
    tracker: Tracker,
    reminders: ReminderService,
    checkpointer: Any,
) -> MeetingPipeline:
    return MeetingPipeline(
        memory=memory,
        extractor=extractor,
        judge=judge,
        tracker=tracker,
        reminders=reminders,
        checkpointer=checkpointer,
    )
