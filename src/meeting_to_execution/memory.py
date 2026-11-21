from __future__ import annotations

import math
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select

from .db import Database
from .embeddings import EmbeddingProvider
from .models import Conflict as ConflictRow
from .models import Decision as DecisionRow
from .models import Digest as DigestRow
from .models import Meeting as MeetingRow
from .models import Notification as NotificationRow
from .models import Reminder as ReminderRow
from .models import Review as ReviewRow
from .models import Task as TaskRow
from .normalization import decision_embedding_text, norm_text
from .schemas import (
    Attendee,
    CandidateView,
    ConflictView,
    DecisionRecord,
    DecisionView,
    DigestView,
    MeetingCreate,
    MeetingView,
    NormalizedItem,
    NotificationView,
    ReminderView,
    ReviewSubmission,
    StatsView,
    TaskDraft,
    TaskView,
)
from .vector_index import VectorIndex


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _owner_name(attendees: list[dict[str, Any]], owner_id: str | None) -> str | None:
    if not owner_id:
        return None
    for attendee in attendees:
        if attendee.get("id") == owner_id:
            return attendee.get("name") or owner_id
    return owner_id


class MemoryStore:
    def __init__(self, db: Database, vector: VectorIndex, embeddings: EmbeddingProvider):
        self.db = db
        self.vector = vector
        self.embeddings = embeddings

    # ------------------------------------------------------------------
    # Meetings
    # ------------------------------------------------------------------
    def save_meeting(self, meeting: MeetingCreate, meeting_id: str) -> MeetingView:
        with self.db.session() as session:
            existing = session.get(MeetingRow, meeting_id)
            if existing:
                raise ValueError(f"meeting already exists: {meeting_id}")
            row = MeetingRow(
                id=meeting_id,
                title=meeting.title,
                meeting_date=meeting.meeting_date,
                attendees=[a.model_dump(mode="json") for a in meeting.attendees],
                transcript=meeting.transcript,
                status="processing",
            )
            session.add(row)
            session.flush()
            return self._meeting_view(session, row)

    def get_meeting(self, meeting_id: str) -> MeetingView | None:
        with self.db.session() as session:
            row = session.get(MeetingRow, meeting_id)
            return self._meeting_view(session, row) if row else None

    def list_meetings(self, limit: int = 100) -> list[MeetingView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(MeetingRow).order_by(MeetingRow.meeting_date.desc()).limit(limit)
            ).all()
            return [self._meeting_view(session, row) for row in rows]

    def set_meeting_status(self, meeting_id: str, status: str) -> None:
        with self.db.session() as session:
            row = session.get(MeetingRow, meeting_id)
            if row:
                row.status = status

    def _meeting_view(self, session, row: MeetingRow) -> MeetingView:
        decision_count = (
            session.scalar(
                select(func.count())
                .select_from(DecisionRow)
                .where(DecisionRow.meeting_id == row.id)
            )
            or 0
        )
        task_count = (
            session.scalar(
                select(func.count()).select_from(TaskRow).where(TaskRow.meeting_id == row.id)
            )
            or 0
        )
        return MeetingView(
            id=row.id,
            title=row.title,
            meeting_date=row.meeting_date,
            attendees=[Attendee(**attendee) for attendee in row.attendees or []],
            transcript=row.transcript,
            status=row.status,
            created_at=_aware(row.created_at),
            decision_count=decision_count,
            task_count=task_count,
        )

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def candidates(
        self,
        item: NormalizedItem,
        meeting_id: str | None = None,
        limit: int = 8,
    ) -> list[CandidateView]:
        query_text = decision_embedding_text(item)
        query_embedding = self.embeddings.embed(query_text)
        vector_hits = dict(self.vector.search(query_embedding, limit=max(limit * 4, 20)))

        with self.db.session() as session:
            conditions = [DecisionRow.status == "active"]
            if meeting_id:
                conditions.append(DecisionRow.meeting_id != meeting_id)
            structured_conditions = [func.lower(DecisionRow.subject) == norm_text(item.subject)]
            if item.attribute:
                structured_conditions.append(
                    func.lower(DecisionRow.attribute) == norm_text(item.attribute)
                )
            conditions.append(or_(*structured_conditions))
            structured = session.scalars(
                select(DecisionRow).where(*conditions).limit(max(limit * 2, 12))
            ).all()
            structured_ids = {row.id for row in structured}

            all_ids = set(vector_hits) | structured_ids
            if not all_ids:
                return []

            rows = session.scalars(
                select(DecisionRow).where(
                    DecisionRow.id.in_(all_ids),
                    DecisionRow.status == "active",
                )
            ).all()
            meeting_titles = {
                m.id: m.title
                for m in session.scalars(
                    select(MeetingRow).where(
                        MeetingRow.id.in_({row.meeting_id for row in rows})
                    )
                ).all()
            }

            candidates: list[CandidateView] = []
            for row in rows:
                similarity = vector_hits.get(row.id)
                if similarity is None:
                    similarity = self._cosine(
                        query_embedding,
                        self.embeddings.embed(decision_embedding_text(self._normalized_from_row(row))),
                    )
                matched_subject = norm_text(row.subject) == norm_text(item.subject)
                candidates.append(
                    CandidateView(
                        decision=self._decision_view(row, meeting_titles.get(row.meeting_id)),
                        similarity=similarity,
                        matched_subject=matched_subject,
                    )
                )

            candidates.sort(
                key=lambda c: (c.matched_subject, c.similarity), reverse=True
            )
            return candidates[:limit]

    def list_decisions(
        self,
        status: str | None = None,
        meeting_id: str | None = None,
        limit: int = 200,
    ) -> list[DecisionView]:
        with self.db.session() as session:
            query = select(DecisionRow, MeetingRow).join(
                MeetingRow, MeetingRow.id == DecisionRow.meeting_id
            )
            if status:
                query = query.where(DecisionRow.status == status)
            if meeting_id:
                query = query.where(DecisionRow.meeting_id == meeting_id)
            rows = session.execute(
                query.order_by(DecisionRow.created_at.desc()).limit(limit)
            ).all()
            return [
                self._decision_view(row, meeting_row.title)
                for row, meeting_row in rows
            ]

    def get_decision(self, decision_id: str) -> DecisionView | None:
        with self.db.session() as session:
            row = session.get(DecisionRow, decision_id)
            if not row:
                return None
            meeting = session.get(MeetingRow, row.meeting_id)
            return self._decision_view(row, meeting.title if meeting else None)

    def commit_plan(
        self,
        *,
        thread_id: str,
        meeting_id: str,
        decisions: list[DecisionRecord],
        tasks: list[TaskDraft],
        superseded: set[str],
        duplicate_of: dict[str, str],
        conflicts: list[ConflictView],
        review: ReviewSubmission,
    ) -> list[TaskDraft]:
        saved_tasks: list[TaskDraft] = []
        with self.db.session() as session:
            for old_id in superseded:
                old = session.get(DecisionRow, old_id)
                if old:
                    old.status = "superseded"

            for decision in decisions:
                row = session.get(DecisionRow, decision.id)
                payload = {
                    "meeting_id": meeting_id,
                    "meeting_date": decision.meeting_date,
                    "type": decision.type,
                    "subject": decision.subject,
                    "attribute": decision.attribute,
                    "value": decision.value,
                    "owner_id": decision.owner,
                    "owner_text": decision.owner_text,
                    "deadline": decision.deadline,
                    "deadline_text": decision.deadline_text,
                    "confidence": decision.confidence,
                    "source_quote": decision.source_quote,
                    "speaker": decision.speaker,
                    "flags": decision.flags,
                    "status": decision.status,
                    "supersedes": decision.supersedes,
                }
                if row:
                    for key, value in payload.items():
                        setattr(row, key, value)
                else:
                    session.add(DecisionRow(id=decision.id, **payload))

            for task in tasks:
                existing = session.scalar(
                    select(TaskRow).where(TaskRow.dedupe_key == task.dedupe_key)
                )
                if existing and existing.id != task.id:
                    continue
                row = session.get(TaskRow, task.id)
                if row:
                    row.title = task.title
                    row.body = task.body
                    row.owner_id = task.owner_id
                    row.deadline = task.deadline
                    row.status = task.status
                    row.duplicate_of = task.duplicate_of
                else:
                    session.add(
                        TaskRow(
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
                    )
                saved_tasks.append(task)

            for conflict in conflicts:
                if conflict.relation == "unrelated":
                    continue
                exists = session.scalar(
                    select(ConflictRow).where(
                        ConflictRow.new_item_id == conflict.new_item_id,
                        ConflictRow.old_decision_id == conflict.old_decision_id,
                    )
                )
                if not exists:
                    session.add(
                        ConflictRow(
                            meeting_id=meeting_id,
                            new_item_id=conflict.new_item_id,
                            old_decision_id=conflict.old_decision_id,
                            relation=conflict.relation,
                            reasoning=conflict.reasoning,
                            confidence=conflict.confidence,
                        )
                    )

            review_row = session.get(ReviewRow, thread_id)
            if review_row:
                review_row.status = "approved"
                review_row.submitted = review.model_dump(mode="json")
                review_row.note = review.note
                review_row.reviewer = review.reviewer
                review_row.decided_at = datetime.now(UTC)

            meeting = session.get(MeetingRow, meeting_id)
            if meeting:
                meeting.status = "approved"

        for decision in decisions:
            self.vector.upsert(
                decision.id, self.embeddings.embed(decision_embedding_text(decision))
            )
        return saved_tasks

    def reject_review(
        self,
        *,
        thread_id: str,
        meeting_id: str,
        review: ReviewSubmission,
    ) -> None:
        with self.db.session() as session:
            row = session.get(ReviewRow, thread_id)
            if row:
                row.status = "rejected"
                row.submitted = review.model_dump(mode="json")
                row.note = review.note
                row.reviewer = review.reviewer
                row.decided_at = datetime.now(UTC)
            meeting = session.get(MeetingRow, meeting_id)
            if meeting:
                meeting.status = "rejected"

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------
    def save_pending_review(
        self, *, thread_id: str, meeting_id: str, packet_hash: str
    ) -> None:
        with self.db.session() as session:
            row = session.get(ReviewRow, thread_id)
            if row:
                row.packet_hash = packet_hash
                row.status = "pending"
                return
            session.add(
                ReviewRow(
                    thread_id=thread_id,
                    meeting_id=meeting_id,
                    packet_hash=packet_hash,
                    status="pending",
                )
            )

    def get_review(self, thread_id: str) -> ReviewRow | None:
        with self.db.session() as session:
            return session.get(ReviewRow, thread_id)

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def find_task_duplicate(
        self, task: TaskDraft, exclude_id: str | None = None
    ) -> TaskView | None:
        with self.db.session() as session:
            query = select(TaskRow).where(TaskRow.dedupe_key == task.dedupe_key)
            if exclude_id:
                query = query.where(TaskRow.id != exclude_id)
            exact = session.scalar(query)
            if exact:
                return self._task_view(session, exact)

            rows = session.scalars(
                select(TaskRow).where(TaskRow.owner_id == task.owner_id)
            ).all()
            target = norm_text(task.title)
            for row in rows:
                if exclude_id and row.id == exclude_id:
                    continue
                ratio = SequenceMatcher(None, target, norm_text(row.title)).ratio()
                if ratio >= 0.9:
                    return self._task_view(session, row)
        return None

    def save_task(self, task: TaskDraft) -> TaskView:
        with self.db.session() as session:
            row = session.get(TaskRow, task.id)
            if not row:
                row = TaskRow(
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
                session.add(row)
            else:
                row.title = task.title
                row.body = task.body
                row.owner_id = task.owner_id
                row.deadline = task.deadline
                row.status = task.status
                row.duplicate_of = task.duplicate_of
            session.flush()
            return self._task_view(session, row)

    def update_task_posted(
        self, task_id: str, tracker_ref: str, tracker_provider: str
    ) -> TaskView | None:
        with self.db.session() as session:
            row = session.get(TaskRow, task_id)
            if not row:
                return None
            row.status = "posted"
            row.tracker_ref = tracker_ref
            row.tracker_provider = tracker_provider
            row.posted_at = datetime.now(UTC)
            session.flush()
            return self._task_view(session, row)

    def get_task(self, task_id: str) -> TaskView | None:
        with self.db.session() as session:
            row = session.get(TaskRow, task_id)
            return self._task_view(session, row) if row else None

    def list_tasks(
        self,
        status: str | None = None,
        owner_id: str | None = None,
        limit: int = 200,
    ) -> list[TaskView]:
        with self.db.session() as session:
            query = select(TaskRow)
            if status:
                query = query.where(TaskRow.status == status)
            if owner_id:
                query = query.where(TaskRow.owner_id == owner_id)
            rows = session.scalars(
                query.order_by(TaskRow.created_at.desc()).limit(limit)
            ).all()
            meetings = {
                row.id: row
                for row in session.scalars(
                    select(MeetingRow).where(
                        MeetingRow.id.in_({task.meeting_id for task in rows})
                    )
                ).all()
            }
            return [
                self._task_view(session, task, meetings.get(task.meeting_id))
                for task in rows
            ]

    def tasks_between(self, start: date, end: date) -> list[TaskView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(TaskRow).where(
                    TaskRow.deadline >= start,
                    TaskRow.deadline <= end,
                    TaskRow.status.in_(["approved", "posted"]),
                )
            ).all()
            meetings = {
                row.id: row
                for row in session.scalars(
                    select(MeetingRow).where(
                        MeetingRow.id.in_({task.meeting_id for task in rows})
                    )
                ).all()
            }
            return [
                self._task_view(session, task, meetings.get(task.meeting_id))
                for task in rows
            ]

    # ------------------------------------------------------------------
    # Conflicts
    # ------------------------------------------------------------------
    def list_conflicts(
        self,
        limit: int = 200,
        meeting_id: str | None = None,
        decision_id: str | None = None,
        relation: str | None = None,
    ) -> list[ConflictView]:
        with self.db.session() as session:
            query = select(ConflictRow)
            if meeting_id:
                query = query.where(ConflictRow.meeting_id == meeting_id)
            if decision_id:
                query = query.where(
                    or_(
                        ConflictRow.new_item_id == decision_id,
                        ConflictRow.old_decision_id == decision_id,
                    )
                )
            if relation:
                query = query.where(ConflictRow.relation == relation)
            rows = session.scalars(
                query.order_by(ConflictRow.created_at.desc()).limit(limit)
            ).all()
            return [
                ConflictView(
                    id=row.id,
                    new_item_id=row.new_item_id,
                    old_decision_id=row.old_decision_id,
                    relation=row.relation,
                    reasoning=row.reasoning,
                    confidence=row.confidence,
                    meeting_id=row.meeting_id,
                    created_at=_aware(row.created_at),
                )
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Reminders, digests, notifications
    # ------------------------------------------------------------------
    def create_reminder(
        self, task_id: str, scheduled_for: datetime, message: str, channel: str = "log"
    ) -> ReminderView:
        with self.db.session() as session:
            row = session.scalar(
                select(ReminderRow).where(
                    ReminderRow.task_id == task_id,
                    ReminderRow.scheduled_for == scheduled_for,
                )
            )
            if not row:
                row = ReminderRow(
                    task_id=task_id,
                    scheduled_for=scheduled_for,
                    message=message,
                    channel=channel,
                )
                session.add(row)
                session.flush()
            return self._reminder_view(row)

    def list_reminders(self, limit: int = 200) -> list[ReminderView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(ReminderRow).order_by(ReminderRow.scheduled_for.desc()).limit(limit)
            ).all()
            return [self._reminder_view(row) for row in rows]

    def pending_reminders(self, now: datetime, limit: int = 200) -> list[ReminderView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(ReminderRow)
                .where(
                    ReminderRow.status == "scheduled",
                    ReminderRow.scheduled_for <= now,
                )
                .order_by(ReminderRow.scheduled_for)
                .limit(limit)
            ).all()
            return [self._reminder_view(row) for row in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self.db.session() as session:
            row = session.get(ReminderRow, reminder_id)
            if row:
                row.status = "sent"
                row.sent_at = datetime.now(UTC)

    def mark_digest_sent(self, digest_id: int) -> None:
        with self.db.session() as session:
            row = session.get(DigestRow, digest_id)
            if row:
                row.sent_at = datetime.now(UTC)

    def save_digest(
        self,
        owner_id: str,
        week_start: date,
        week_end: date,
        text: str,
    ) -> DigestView:
        with self.db.session() as session:
            row = session.scalar(
                select(DigestRow).where(
                    DigestRow.owner_id == owner_id,
                    DigestRow.week_start == week_start,
                    DigestRow.week_end == week_end,
                )
            )
            if not row:
                row = DigestRow(
                    owner_id=owner_id,
                    week_start=week_start,
                    week_end=week_end,
                    text=text,
                )
                session.add(row)
                session.flush()
            else:
                row.text = text
            return self._digest_view(session, row)

    def list_digests(self, limit: int = 100) -> list[DigestView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(DigestRow).order_by(DigestRow.created_at.desc()).limit(limit)
            ).all()
            return [self._digest_view(session, row) for row in rows]

    def save_notification(
        self,
        *,
        kind: str,
        recipient: str,
        subject: str,
        body: str,
        channel: str = "log",
        status: str = "sent",
    ) -> NotificationView:
        with self.db.session() as session:
            row = NotificationRow(
                kind=kind,
                recipient=recipient,
                subject=subject,
                body=body,
                channel=channel,
                status=status,
                sent_at=datetime.now(UTC) if status == "sent" else None,
            )
            session.add(row)
            session.flush()
            return self._notification_view(row)

    def list_notifications(self, limit: int = 100) -> list[NotificationView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(NotificationRow)
                .order_by(NotificationRow.created_at.desc())
                .limit(limit)
            ).all()
            return [self._notification_view(row) for row in rows]

    def owner_name(self, owner_id: str) -> str | None:
        with self.db.session() as session:
            meetings = session.scalars(select(MeetingRow)).all()
            for meeting in meetings:
                name = _owner_name(meeting.attendees or [], owner_id)
                if name:
                    return name
        return owner_id

    def stats(self) -> StatsView:
        with self.db.session() as session:
            meetings = session.scalar(select(func.count()).select_from(MeetingRow)) or 0
            active = (
                session.scalar(
                    select(func.count())
                    .select_from(DecisionRow)
                    .where(DecisionRow.status == "active")
                )
                or 0
            )
            superseded = (
                session.scalar(
                    select(func.count())
                    .select_from(DecisionRow)
                    .where(DecisionRow.status == "superseded")
                )
                or 0
            )
            tasks = session.scalar(select(func.count()).select_from(TaskRow)) or 0
            posted = (
                session.scalar(
                    select(func.count())
                    .select_from(TaskRow)
                    .where(TaskRow.status == "posted")
                )
                or 0
            )
            conflicts = (
                session.scalar(
                    select(func.count())
                    .select_from(ConflictRow)
                    .where(ConflictRow.relation.in_(["contradicts", "refines"]))
                )
                or 0
            )
        return StatsView(
            meetings=meetings,
            active_decisions=active,
            superseded_decisions=superseded,
            tasks=tasks,
            posted_tasks=posted,
            open_conflicts=conflicts,
        )

    # ------------------------------------------------------------------
    # Conversions
    # ------------------------------------------------------------------
    def _normalized_from_row(self, row: DecisionRow) -> NormalizedItem:
        return NormalizedItem(
            id=row.id,
            type=row.type,
            subject=row.subject,
            attribute=row.attribute,
            value=row.value,
            owner=row.owner_id,
            owner_text=row.owner_text,
            deadline=row.deadline,
            deadline_text=row.deadline_text,
            confidence=row.confidence,
            source_quote=row.source_quote,
            speaker=row.speaker,
            flags=row.flags or [],
        )

    def _decision_view(self, row: DecisionRow, meeting_title: str | None) -> DecisionView:
        return DecisionView(
            **self._normalized_from_row(row).model_dump(),
            status=row.status,
            meeting_id=row.meeting_id,
            meeting_title=meeting_title,
            meeting_date=row.meeting_date,
            created_at=_aware(row.created_at),
            supersedes=row.supersedes or [],
        )

    def _task_view(
        self, session, row: TaskRow, meeting: MeetingRow | None = None
    ) -> TaskView:
        if meeting is None:
            meeting = session.get(MeetingRow, row.meeting_id)
        attendees = meeting.attendees if meeting else []
        return TaskView(
            id=row.id,
            meeting_id=row.meeting_id,
            item_id=row.item_id,
            title=row.title,
            body=row.body,
            owner_id=row.owner_id,
            owner_name=_owner_name(attendees or [], row.owner_id),
            deadline=row.deadline,
            status=row.status,
            dedupe_key=row.dedupe_key,
            idempotency_key=row.idempotency_key,
            tracker_ref=row.tracker_ref,
            tracker_provider=row.tracker_provider,
            duplicate_of=row.duplicate_of,
            created_at=_aware(row.created_at),
            posted_at=_aware(row.posted_at),
        )

    @staticmethod
    def _reminder_view(row: ReminderRow) -> ReminderView:
        return ReminderView(
            id=row.id,
            task_id=row.task_id,
            scheduled_for=_aware(row.scheduled_for),
            sent_at=_aware(row.sent_at),
            status=row.status,
            channel=row.channel,
            message=row.message,
        )

    def _digest_view(self, session, row: DigestRow) -> DigestView:
        name = self.owner_name(row.owner_id)
        return DigestView(
            id=row.id,
            owner_id=row.owner_id,
            owner_name=name,
            week_start=row.week_start,
            week_end=row.week_end,
            text=row.text,
            created_at=_aware(row.created_at),
            sent_at=_aware(row.sent_at),
        )

    @staticmethod
    def _notification_view(row: NotificationRow) -> NotificationView:
        return NotificationView(
            id=row.id,
            kind=row.kind,
            recipient=row.recipient,
            subject=row.subject,
            body=row.body,
            channel=row.channel,
            status=row.status,
            created_at=_aware(row.created_at),
            sent_at=_aware(row.sent_at),
        )

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))
