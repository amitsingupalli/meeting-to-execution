from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ItemType = Literal["decision", "action_item"]
DecisionStatus = Literal["active", "superseded", "revoked"]
TaskStatus = Literal["proposed", "approved", "skipped", "posted"]
ConflictRelation = Literal["contradicts", "refines", "duplicates", "unrelated"]
ConflictAction = Literal["supersede", "keep_both", "duplicate", "none"]
ReviewAction = Literal["approve", "edit", "reject"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Attendee(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list)
    email: str | None = None


class MeetingCreate(BaseModel):
    meeting_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    meeting_date: date
    attendees: list[Attendee] = Field(min_length=1)
    transcript: str = Field(min_length=1)

    @model_validator(mode="after")
    def _attendee_ids_unique(self):
        ids = [a.id for a in self.attendees]
        if len(ids) != len(set(ids)):
            raise ValueError("attendee ids must be unique")
        return self


class MeetingView(ORMModel):
    id: str
    title: str
    meeting_date: date
    attendees: list[Attendee]
    transcript: str
    status: str
    created_at: datetime
    decision_count: int = 0
    task_count: int = 0


class ExtractedItem(BaseModel):
    type: ItemType
    subject: str
    attribute: str | None = None
    value: str | None = None
    owner: str | None = None
    deadline: str | None = None
    confidence: float = Field(ge=0, le=1)
    source_quote: str
    speaker: str | None = None


class ExtractionEnvelope(BaseModel):
    items: list[ExtractedItem] = Field(default_factory=list)


class NormalizedItem(BaseModel):
    id: str
    type: ItemType
    subject: str
    attribute: str | None = None
    value: str | None = None
    owner: str | None = None
    owner_text: str | None = None
    deadline: date | None = None
    deadline_text: str | None = None
    confidence: float = Field(ge=0, le=1)
    source_quote: str
    speaker: str | None = None
    flags: list[str] = Field(default_factory=list)


class DecisionView(NormalizedItem):
    status: DecisionStatus
    meeting_id: str
    meeting_title: str | None = None
    meeting_date: date
    created_at: datetime
    supersedes: list[str] = Field(default_factory=list)


class TaskView(BaseModel):
    id: str
    meeting_id: str
    item_id: str
    title: str
    body: str
    owner_id: str | None = None
    owner_name: str | None = None
    deadline: date | None = None
    status: TaskStatus
    dedupe_key: str
    idempotency_key: str
    tracker_ref: str | None = None
    tracker_provider: str | None = None
    duplicate_of: str | None = None
    created_at: datetime
    posted_at: datetime | None = None


class CandidateView(BaseModel):
    decision: DecisionView
    similarity: float = Field(ge=0, le=1)
    matched_subject: bool = False


class ConflictView(BaseModel):
    id: int | None = None
    new_item_id: str
    old_decision_id: str
    relation: ConflictRelation
    reasoning: str
    confidence: float = Field(ge=0, le=1)
    meeting_id: str | None = None
    created_at: datetime | None = None


class ReviewConflictView(BaseModel):
    verdict: ConflictView
    old_decision: DecisionView


class ReviewDecisionView(BaseModel):
    item: NormalizedItem
    conflicts: list[ReviewConflictView] = Field(default_factory=list)
    proposed_conflict_action: ConflictAction = "none"
    proposed_duplicate_of: str | None = None


class ReviewPacket(BaseModel):
    thread_id: str
    meeting: MeetingView
    decisions: list[ReviewDecisionView] = Field(default_factory=list)
    tasks: list[TaskView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    packet_hash: str
    generated_at: datetime


class EditableDecision(BaseModel):
    subject: str = Field(min_length=1)
    attribute: str | None = None
    value: str | None = None
    owner_id: str | None = None
    deadline: date | None = None
    confidence: float = Field(ge=0, le=1)


class EditableTask(BaseModel):
    title: str = Field(min_length=1)
    body: str | None = None
    owner_id: str | None = None
    deadline: date | None = None


class DecisionReviewInput(BaseModel):
    item_id: str
    action: ReviewAction
    edited: EditableDecision | None = None
    conflict_action: ConflictAction = "none"

    @model_validator(mode="after")
    def _edit_required(self):
        if self.action == "edit" and self.edited is None:
            raise ValueError("edited decision is required when action=edit")
        return self


class TaskReviewInput(BaseModel):
    task_id: str
    action: ReviewAction
    edited: EditableTask | None = None

    @model_validator(mode="after")
    def _edit_required(self):
        if self.action == "edit" and self.edited is None:
            raise ValueError("edited task is required when action=edit")
        return self


class ReviewSubmission(BaseModel):
    packet_hash: str
    status: Literal["approved", "rejected"]
    decisions: list[DecisionReviewInput] = Field(default_factory=list)
    tasks: list[TaskReviewInput] = Field(default_factory=list)
    note: str | None = None
    reviewer: str | None = None


class ReviewResult(BaseModel):
    thread_id: str
    status: str
    decisions_saved: int = 0
    tasks_posted: int = 0
    tasks_skipped: int = 0
    superseded: list[str] = Field(default_factory=list)
    reminders: list[dict] = Field(default_factory=list)
    posted_tasks: list[TaskView] = Field(default_factory=list)
    message: str


class IngestResponse(BaseModel):
    thread_id: str
    status: str
    review: ReviewPacket


class ConflictDetail(BaseModel):
    conflict: ConflictView
    new_item: NormalizedItem
    old_decision: DecisionView


class ReminderView(BaseModel):
    id: int
    task_id: str
    scheduled_for: datetime
    sent_at: datetime | None = None
    status: str
    channel: str
    message: str


class DigestView(BaseModel):
    id: int
    owner_id: str
    owner_name: str | None = None
    week_start: date
    week_end: date
    text: str
    created_at: datetime
    sent_at: datetime | None = None


class NotificationView(BaseModel):
    id: int
    kind: str
    recipient: str
    subject: str
    body: str
    channel: str
    status: str
    created_at: datetime
    sent_at: datetime | None = None


class StatsView(BaseModel):
    meetings: int
    active_decisions: int
    superseded_decisions: int
    tasks: int
    posted_tasks: int
    open_conflicts: int


class HealthView(BaseModel):
    status: str
    app: str
    environment: str
    llm_enabled: bool
    tracker_backend: str
    database: str
