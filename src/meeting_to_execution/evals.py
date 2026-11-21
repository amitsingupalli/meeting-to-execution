from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Settings
from .container import Container
from .schemas import (
    DecisionReviewInput,
    MeetingCreate,
    ReviewSubmission,
    TaskReviewInput,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = PROJECT_ROOT / "fixtures" / "meetings"
DEFAULT_GOLDEN = PROJECT_ROOT / "evals" / "golden.json"


class ExpectedDecision(BaseModel):
    subject: str
    attribute: str | None = None
    value: str | None = None


class ExpectedAction(BaseModel):
    subject: str
    owner: str | None = None
    deadline: str | None = None


class ExpectedMeeting(BaseModel):
    file: str
    decisions: list[ExpectedDecision] = Field(default_factory=list)
    actions: list[ExpectedAction] = Field(default_factory=list)
    forbidden_subjects: list[str] = Field(default_factory=list)


class ExpectedConflict(BaseModel):
    file: str
    subject: str
    old_value: str | None
    relation: str


class GoldenSet(BaseModel):
    meetings: list[ExpectedMeeting]
    conflicts: list[ExpectedConflict] = Field(default_factory=list)


class EvalReport(BaseModel):
    passed: bool
    meetings: int
    extraction_precision: float
    extraction_recall: float
    conflict_precision: float
    conflict_recall: float
    expected_items: int
    matched_items: int
    expected_conflicts: int
    matched_conflicts: int
    failures: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


def _ratio(matched: int, total: int) -> float:
    return round(matched / total, 4) if total else 1.0


def _review(packet) -> ReviewSubmission:
    return ReviewSubmission(
        packet_hash=packet.packet_hash,
        status="approved",
        decisions=[
            DecisionReviewInput(
                item_id=entry.item.id,
                action="approve",
                conflict_action=entry.proposed_conflict_action,
            )
            for entry in packet.decisions
        ],
        tasks=[
            TaskReviewInput(task_id=task.id, action="approve") for task in packet.tasks
        ],
        reviewer="eval-harness",
    )


def load_golden(path: str | Path = DEFAULT_GOLDEN) -> GoldenSet:
    return GoldenSet.model_validate(json.loads(Path(path).read_text()))


def run_evals(
    fixtures_dir: str | Path = DEFAULT_FIXTURES,
    golden_path: str | Path = DEFAULT_GOLDEN,
) -> EvalReport:
    fixtures = Path(fixtures_dir)
    golden = load_golden(golden_path)
    failures: list[str] = []

    expected_decisions: Counter = Counter()
    expected_actions: Counter = Counter()
    forbidden: set[tuple[str, str]] = set()
    expected_conflicts: Counter = Counter()

    for meeting in golden.meetings:
        for decision in meeting.decisions:
            expected_decisions[
                (meeting.file, decision.subject, decision.attribute, decision.value)
            ] += 1
        for action in meeting.actions:
            expected_actions[
                (meeting.file, action.subject, action.owner, action.deadline)
            ] += 1
        for subject in meeting.forbidden_subjects:
            forbidden.add((meeting.file, subject))
    for conflict in golden.conflicts:
        expected_conflicts[
            (conflict.file, conflict.subject, conflict.old_value, conflict.relation)
        ] += 1

    predicted_decisions: Counter = Counter()
    predicted_actions: Counter = Counter()
    predicted_conflicts: Counter = Counter()

    with tempfile.TemporaryDirectory(prefix="mte-evals-") as tmp:
        settings = Settings(
            app_env="eval",
            database_url=f"sqlite:///{Path(tmp) / 'evals.db'}",
            checkpoint_db=str(Path(tmp) / "checkpoints.db"),
            enable_scheduler=False,
            tracker_backend="mock",
            vector_backend="json",
            llm_api_key=None,
        )
        container = Container.build(settings)
        try:
            for index, expected in enumerate(golden.meetings, start=1):
                path = fixtures / expected.file
                meeting = MeetingCreate.model_validate(json.loads(path.read_text()))
                thread_id = f"eval-{index}"
                container.pipeline.start(meeting, thread_id)
                packet = container.pipeline.get_packet(thread_id)
                if packet is None:
                    failures.append(f"{expected.file}: pipeline produced no review packet")
                    continue

                for entry in packet.decisions:
                    item = entry.item
                    predicted_decisions[
                        (expected.file, item.subject, item.attribute, item.value)
                    ] += 1
                    if (expected.file, item.subject) in forbidden:
                        failures.append(
                            f"{expected.file}: forbidden non-decision extracted: "
                            f"{item.subject!r}"
                        )
                    for conflict in entry.conflicts:
                        if conflict.verdict.relation == "unrelated":
                            continue
                        predicted_conflicts[
                            (
                                expected.file,
                                item.subject,
                                conflict.old_decision.value,
                                conflict.verdict.relation,
                            )
                        ] += 1

                for task in packet.tasks:
                    predicted_actions[
                        (
                            expected.file,
                            task.title,
                            task.owner_id,
                            task.deadline.isoformat() if task.deadline else None,
                        )
                    ] += 1

                container.pipeline.resume(thread_id, _review(packet))
            stats = container.memory.stats().model_dump()
        finally:
            container.close()

    for key, count in (expected_decisions - predicted_decisions).items():
        failures.append(f"missing decision x{count}: {key}")
    for key, count in (predicted_decisions - expected_decisions).items():
        failures.append(f"unexpected decision x{count}: {key}")
    for key, count in (expected_actions - predicted_actions).items():
        failures.append(f"missing action x{count}: {key}")
    for key, count in (predicted_actions - expected_actions).items():
        failures.append(f"unexpected action x{count}: {key}")
    for key, count in (expected_conflicts - predicted_conflicts).items():
        failures.append(f"missing conflict x{count}: {key}")
    for key, count in (predicted_conflicts - expected_conflicts).items():
        failures.append(f"unexpected conflict x{count}: {key}")

    expected_items = sum(expected_decisions.values()) + sum(expected_actions.values())
    predicted_items = sum(predicted_decisions.values()) + sum(predicted_actions.values())
    matched_items = sum((expected_decisions & predicted_decisions).values()) + sum(
        (expected_actions & predicted_actions).values()
    )
    expected_conflict_count = sum(expected_conflicts.values())
    predicted_conflict_count = sum(predicted_conflicts.values())
    matched_conflicts = sum((expected_conflicts & predicted_conflicts).values())

    return EvalReport(
        passed=not failures,
        meetings=len(golden.meetings),
        extraction_precision=_ratio(matched_items, predicted_items),
        extraction_recall=_ratio(matched_items, expected_items),
        conflict_precision=_ratio(matched_conflicts, predicted_conflict_count),
        conflict_recall=_ratio(matched_conflicts, expected_conflict_count),
        expected_items=expected_items,
        matched_items=matched_items,
        expected_conflicts=expected_conflict_count,
        matched_conflicts=matched_conflicts,
        failures=failures,
        stats=stats,
    )
