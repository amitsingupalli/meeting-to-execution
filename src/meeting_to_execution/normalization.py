from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time

import dateparser

from .schemas import ExtractedItem, MeetingCreate, NormalizedItem, TaskDraft


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def stable_id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def quote_is_present(quote: str, transcript: str) -> bool:
    normalized_quote = norm_text(quote)
    return bool(normalized_quote) and normalized_quote in norm_text(transcript)


EXPLICIT_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|\d{1,2}/\d{1,2})\b",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(
    r"\b(?:next|this|coming|tomorrow|today|tonight|end of|in\s+\d+)\b",
    re.IGNORECASE,
)


def resolve_deadline(raw: str | None, meeting_date: date) -> date | None:
    """Resolve a relative deadline against the meeting date, never today."""
    if not raw or not raw.strip():
        return None
    relative_base = datetime.combine(meeting_date, time(17, 0))
    parsed = dateparser.parse(
        raw,
        settings={
            "RELATIVE_BASE": relative_base,
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if not parsed:
        return None
    if EXPLICIT_DATE_RE.search(raw) and not RELATIVE_DATE_RE.search(raw):
        # Pick the occurrence closest to the meeting date. This keeps
        # "November 6" in a November 9 meeting as a past deadline to flag,
        # while "January 9" in November resolves to the next January.
        candidates = [
            date(parsed.year + offset, parsed.month, parsed.day)
            for offset in (-1, 0, 1)
        ]
        return min(candidates, key=lambda candidate: abs((candidate - meeting_date).days))
    return parsed.date()


def build_owner_map(meeting: MeetingCreate) -> dict[str, str]:
    owner_map: dict[str, str] = {}
    for attendee in meeting.attendees:
        for label in [attendee.id, attendee.name, *attendee.aliases]:
            if label.strip():
                owner_map[norm_text(label)] = attendee.id
    return owner_map


def normalize_items(meeting: MeetingCreate, extracted: list[ExtractedItem]) -> list[NormalizedItem]:
    owner_map = build_owner_map(meeting)
    normalized: list[NormalizedItem] = []

    for raw in extracted:
        subject = raw.subject.strip()
        if not subject:
            continue

        owner_text = raw.owner.strip() if raw.owner else None
        owner_id = owner_map.get(norm_text(owner_text)) if owner_text else None
        flags = list(raw.flags) if hasattr(raw, "flags") else []

        if owner_text and not owner_id:
            flags.append("owner_unresolved")
        if raw.type == "action_item" and not owner_text:
            flags.append("owner_missing")

        deadline_text = raw.deadline.strip() if raw.deadline else None
        deadline = resolve_deadline(deadline_text, meeting.meeting_date)
        if deadline_text and not deadline:
            flags.append("deadline_unresolved")
        if deadline and deadline < meeting.meeting_date:
            flags.append("deadline_before_meeting")

        confidence = raw.confidence
        if not quote_is_present(raw.source_quote, meeting.transcript):
            flags.append("source_quote_not_found")
            confidence = min(confidence, 0.49)

        if raw.type == "decision" and not (raw.value or "").strip():
            flags.append("decision_value_missing")

        speaker = owner_map.get(norm_text(raw.speaker), raw.speaker) if raw.speaker else None
        item_id = stable_id(
            meeting.meeting_id or "",
            raw.type,
            subject,
            raw.attribute or "",
            raw.value or "",
            raw.source_quote,
        )
        normalized.append(
            NormalizedItem(
                id=item_id,
                type=raw.type,
                subject=subject,
                attribute=raw.attribute.strip() if raw.attribute else None,
                value=raw.value.strip() if raw.value else None,
                owner=owner_id,
                owner_text=owner_text,
                deadline=deadline,
                deadline_text=deadline_text,
                confidence=confidence,
                source_quote=raw.source_quote,
                speaker=speaker,
                flags=sorted(set(flags)),
            )
        )

    return normalized


def build_task_draft(item: NormalizedItem, meeting: MeetingCreate) -> TaskDraft:
    dedupe_key = stable_id(
        "task",
        norm_text(item.subject),
        item.owner or "",
    )
    deadline_note = item.deadline.isoformat() if item.deadline else "no deadline"
    owner_note = item.owner or item.owner_text or "unassigned"
    body = (
        f"Meeting: {meeting.title} ({meeting.meeting_date.isoformat()})\n"
        f"Owner: {owner_note}\n"
        f"Deadline: {deadline_note}\n"
        f"Source: \"{item.source_quote}\"\n\n"
        "Created from an approved meeting action item."
    )
    return TaskDraft(
        id=stable_id(meeting.meeting_id or "", item.id, "task"),
        meeting_id=meeting.meeting_id or "",
        item_id=item.id,
        title=item.subject,
        body=body,
        owner_id=item.owner,
        deadline=item.deadline,
        dedupe_key=dedupe_key,
        idempotency_key=f"{meeting.meeting_id}:{item.id}",
    )


def decision_embedding_text(item: NormalizedItem) -> str:
    return " | ".join(
        part for part in [item.subject, item.attribute or "", item.value or ""] if part
    )
