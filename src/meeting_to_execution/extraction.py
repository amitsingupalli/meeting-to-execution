from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings
from .llm import LLMClient
from .prompts import EXTRACTION_SYSTEM
from .schemas import ExtractedItem, ExtractionEnvelope, MeetingCreate

UTTERANCE_RE = re.compile(
    r"^\s*(?:\[(?P<time>[0-9:]+)\]\s*)?(?P<speaker>[A-Za-z][A-Za-z0-9 ._'-]{0,40}):\s*(?P<text>.+)$"
)
HEDGES = re.compile(
    r"\b(maybe|might|could|perhaps|possibly|i think|not sure|what if|should we|"
    r"we should consider|let's explore|option)\b",
    re.IGNORECASE,
)
DECISION_CUES = re.compile(
    r"\b(we'll|we will|we're going with|we are going with|we decided|we've decided|"
    r"the decision is|final decision|we will not|we won't|we're not using|we are not using|"
    r"is scheduled for|is confirmed for|moves to|moved to|is delayed by)\b",
    re.IGNORECASE,
)
ACTION_CUES = re.compile(
    r"^\s*(i'll|i will|i'm going to|i am going to|someone should|someone needs to|"
    r"can you|please|action item:|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)? will)\b",
    re.IGNORECASE,
)
BY_DEADLINE_RE = re.compile(
    r"\b(?:by|before)\s+(?P<deadline>[A-Za-z0-9][A-Za-z0-9 ,/'-]*?)(?:[.;]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Utterance:
    speaker: str
    text: str


def parse_transcript(transcript: str) -> list[Utterance]:
    utterances: list[Utterance] = []
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = UTTERANCE_RE.match(line)
        if match:
            utterances.append(
                Utterance(speaker=match.group("speaker").strip(), text=match.group("text").strip())
            )
        elif utterances:
            previous = utterances[-1]
            utterances[-1] = Utterance(
                speaker=previous.speaker, text=f"{previous.text} {line}".strip()
            )
    return utterances


def _clean(value: str) -> str:
    return value.strip().strip(" .,;:")


def _subject_from_attribute(attribute: str) -> str:
    lowered = attribute.lower()
    if "database" in lowered:
        return "database"
    if "cache" in lowered:
        return "caching"
    if "auth" in lowered:
        return "authentication"
    if "cloud" in lowered or "region" in lowered:
        return "deployment"
    if "launch" in lowered:
        return "beta launch"
    if "timeline" in lowered:
        return "timeline"
    return lowered


def _decision_from_text(text: str) -> ExtractedItem | None:
    if HEDGES.search(text) and not re.search(
        r"\b(decided|decision is|final decision)\b", text, re.IGNORECASE
    ):
        return None
    if text.rstrip().endswith("?") and not re.search(
        r"\b(decided|decision is|final decision)\b", text, re.IGNORECASE
    ):
        return None
    if not DECISION_CUES.search(text):
        return None

    match = re.search(
        r"\b(?:we(?:'ll| will)? not use|we won't use|we're not going to use|"
        r"we are not going to use)\s+(?P<old>.+?)\s+as the\s+(?P<attr>.+?)[.;]\s*"
        r"(?:we(?:'ll| will)|we're going to|we are going to)\s+(?:go with|use)\s+"
        r"(?P<value>.+?)(?:\s+instead)?(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        attribute = _clean(match.group("attr"))
        return ExtractedItem(
            type="decision",
            subject=_subject_from_attribute(attribute),
            attribute=attribute,
            value=_clean(match.group("value")),
            confidence=0.88,
            source_quote=text,
        )

    match = re.search(
        r"\b(?:we(?:'re| are) not using|we won't use|we(?:'ll| will) not use)\s+"
        r"(?P<old>.+?)\s+for\s+(?P<attr>.+?)(?:\s+anymore)?[.;]\s*"
        r"(?:we(?:'ll| will)|we're going to|we are going to)\s+use\s+"
        r"(?P<value>.+?)(?:\s+instead)?(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        attribute = _clean(match.group("attr"))
        return ExtractedItem(
            type="decision",
            subject=_subject_from_attribute(attribute),
            attribute="cache provider" if "cach" in attribute.lower() else attribute,
            value=_clean(match.group("value")),
            confidence=0.88,
            source_quote=text,
        )

    match = re.search(
        r"\bwe(?:'ll| will|'re| are)\s+(?:go with|going with)\s+(?P<value>.+?)\s+"
        r"as the\s+(?P<attr>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        attribute = _clean(match.group("attr"))
        return ExtractedItem(
            type="decision",
            subject=_subject_from_attribute(attribute),
            attribute=attribute,
            value=_clean(match.group("value")),
            confidence=0.9,
            source_quote=text,
        )

    match = re.search(
        r"\bwe(?:'ll| will|'re| are)\s+use\s+(?P<value>.+?)\s+for\s+(?P<attr>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        attribute = _clean(match.group("attr"))
        if "cach" in attribute.lower():
            subject, attribute = "caching", "cache provider"
        elif "auth" in attribute.lower():
            subject, attribute = "authentication", "auth provider"
        else:
            subject = _subject_from_attribute(attribute)
        return ExtractedItem(
            type="decision",
            subject=subject,
            attribute=attribute,
            value=_clean(match.group("value")),
            confidence=0.88,
            source_quote=text,
        )

    match = re.search(
        r"\bwe(?:'ll| will|'re| are)\s+deploy\s+on\s+(?P<value>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return ExtractedItem(
            type="decision",
            subject="deployment",
            attribute="cloud provider",
            value=_clean(match.group("value")),
            confidence=0.9,
            source_quote=text,
        )

    match = re.search(
        r"\bwe(?:'ll| will|'re| are)\s+keep\s+(?P<value>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        value = _clean(match.group("value"))
        return ExtractedItem(
            type="decision",
            subject="deployment" if "aws" in value.lower() else "decision",
            attribute="cloud provider" if "aws" in value.lower() else None,
            value=value,
            confidence=0.82,
            source_quote=text,
        )

    match = re.search(
        r"\bthe\s+(?P<subject>.+?)\s+is\s+(?:scheduled|confirmed)\s+for\s+"
        r"(?P<value>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        subject = _clean(match.group("subject"))
        return ExtractedItem(
            type="decision",
            subject=subject,
            attribute="launch date" if "launch" in subject.lower() else "date",
            value=_clean(match.group("value")),
            confidence=0.9,
            source_quote=text,
        )

    match = re.search(
        r"\bthe\s+(?P<subject>.+?)\s+(?:moves?|moved)\s+to\s+(?P<value>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        subject = _clean(match.group("subject"))
        return ExtractedItem(
            type="decision",
            subject=subject,
            attribute="launch date" if "launch" in subject.lower() else "date",
            value=_clean(match.group("value")),
            confidence=0.88,
            source_quote=text,
        )

    match = re.search(
        r"\bthe\s+(?P<subject>.+?)\s+is\s+delayed\s+by\s+(?P<value>.+?)"
        r"(?:\s+because.*)?(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return ExtractedItem(
            type="decision",
            subject=_clean(match.group("subject")),
            attribute="timeline",
            value=f"delayed by {_clean(match.group('value'))}",
            confidence=0.86,
            source_quote=text,
        )

    match = re.search(
        r"\b(?:we(?:'ll| will)|we(?:'re| are) going to|we decided to|"
        r"we've decided to|the decision is|final decision is)\s+(?P<value>.+?)(?:[.;]|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        return ExtractedItem(
            type="decision",
            subject="decision",
            attribute=None,
            value=_clean(match.group("value")),
            confidence=0.62,
            source_quote=text,
        )
    return None


def _action_from_text(text: str, speaker: str) -> ExtractedItem | None:
    if not ACTION_CUES.search(text):
        return None
    if DECISION_CUES.search(text):
        return None

    owner: str | None = None
    task = text
    match = re.match(
        r"^\s*I(?:'ll| will|'m going to| am going to)\s+(?P<task>.+)$",
        text,
        re.IGNORECASE,
    )
    if match:
        owner = speaker
        task = match.group("task")
    else:
        match = re.match(
            r"^\s*someone\s+(?:should|needs to|will|is going to)\s+(?P<task>.+)$",
            text,
            re.IGNORECASE,
        )
        if match:
            task = match.group("task")
        else:
            match = re.match(r"^\s*(?:can you|please)\s+(?P<task>.+)$", text, re.IGNORECASE)
            if match:
                task = match.group("task")
            else:
                match = re.match(r"^\s*action item:\s*(?P<task>.+)$", text, re.IGNORECASE)
                if match:
                    task = match.group("task")
                else:
                    match = re.match(
                        r"^\s*(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+will\s+(?P<task>.+)$",
                        text,
                    )
                    if not match:
                        return None
                    owner = match.group("name")
                    task = match.group("task")

    deadline_match = BY_DEADLINE_RE.search(task)
    deadline = _clean(deadline_match.group("deadline")) if deadline_match else None
    if deadline_match:
        task = f"{task[: deadline_match.start()]} {task[deadline_match.end() :]}"
    task = _clean(re.sub(r"\b(?:by|before)\s*$", "", task))
    if not task:
        return None

    return ExtractedItem(
        type="action_item",
        subject=task,
        attribute=None,
        value=None,
        owner=owner,
        deadline=f"by {deadline}" if deadline else None,
        confidence=0.82,
        source_quote=text,
        speaker=speaker,
    )


class RuleBasedExtractor:
    def extract(self, meeting: MeetingCreate) -> list[ExtractedItem]:
        items: list[ExtractedItem] = []
        for utterance in parse_transcript(meeting.transcript):
            decision = _decision_from_text(utterance.text)
            if decision:
                decision.speaker = utterance.speaker
                items.append(decision)
            action = _action_from_text(utterance.text, utterance.speaker)
            if action:
                items.append(action)
        return items


class LLMExtractor:
    def __init__(self, client: LLMClient):
        self.client = client

    def extract(self, meeting: MeetingCreate) -> list[ExtractedItem]:
        attendees = ", ".join(f"{a.name} ({a.id})" for a in meeting.attendees)
        user = (
            f"Meeting title: {meeting.title}\n"
            f"Meeting date: {meeting.meeting_date.isoformat()}\n"
            f"Attendees: {attendees}\n\n"
            f"Transcript:\n{meeting.transcript}"
        )
        data = self.client.complete_json(system=EXTRACTION_SYSTEM, user=user)
        return ExtractionEnvelope.model_validate(data).items


class HybridExtractor:
    def __init__(self, llm: LLMExtractor, fallback: RuleBasedExtractor):
        self.llm = llm
        self.fallback = fallback

    def extract(self, meeting: MeetingCreate) -> list[ExtractedItem]:
        if self.llm.client.available:
            try:
                items = self.llm.extract(meeting)
                if items:
                    return items
            except Exception:
                pass
        return self.fallback.extract(meeting)


def build_extractor(settings: Settings) -> HybridExtractor:
    return HybridExtractor(LLMExtractor(LLMClient(settings)), RuleBasedExtractor())
