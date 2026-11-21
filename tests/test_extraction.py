from __future__ import annotations

from datetime import date

from conftest import load_meeting

from meeting_to_execution.extraction import RuleBasedExtractor, parse_transcript
from meeting_to_execution.normalization import (
    normalize_items,
    quote_is_present,
    resolve_deadline,
)


def test_parser_keeps_speaker_and_joins_continuations() -> None:
    transcript = "[00:00] Priya: First line.\n[00:05] Marcus: Second line.\ncontinued here."
    utterances = parse_transcript(transcript)
    assert [u.speaker for u in utterances] == ["Priya", "Marcus"]
    assert utterances[1].text == "Second line. continued here."


def test_hedged_discussion_is_not_extracted_as_decisions() -> None:
    meeting = load_meeting("01_platform_planning.json")
    items = normalize_items(meeting, RuleBasedExtractor().extract(meeting))
    decisions = [item for item in items if item.type == "decision"]

    subjects = {item.subject for item in decisions}
    assert "database" in subjects
    assert "deployment" in subjects
    assert "beta launch" in subjects
    # "Maybe Postgres?" and "I think Postgres is a strong option" are discussion.
    assert all("maybe" not in item.value.lower() for item in decisions if item.value)
    assert len([item for item in decisions if item.subject == "database"]) == 1


def test_action_items_resolve_owner_and_deadline() -> None:
    meeting = load_meeting("01_platform_planning.json")
    items = normalize_items(meeting, RuleBasedExtractor().extract(meeting))
    tasks = {item.subject: item for item in items if item.type == "action_item"}

    schema = tasks["draft the schema"]
    assert schema.owner == "usr_priya"
    assert schema.deadline == date(2026, 11, 6)
    assert schema.flags == []

    benchmark = tasks["benchmark Postgres"]
    assert benchmark.owner == "usr_marcus"
    assert benchmark.deadline == date(2026, 11, 9)

    investigate = tasks["investigate authentication options"]
    assert investigate.owner is None
    assert "owner_missing" in investigate.flags


def test_every_extracted_quote_is_verbatim() -> None:
    for name in (
        "01_platform_planning.json",
        "02_platform_followup.json",
        "03_architecture_review.json",
        "04_security_launch.json",
    ):
        meeting = load_meeting(name)
        for item in RuleBasedExtractor().extract(meeting):
            assert quote_is_present(item.source_quote, meeting.transcript), (
                name,
                item.source_quote,
            )


def test_relative_deadline_uses_meeting_date_not_today() -> None:
    assert resolve_deadline("by next Friday", date(2026, 11, 2)) == date(2026, 11, 6)
    assert resolve_deadline("by November 6", date(2026, 11, 9)) == date(2026, 11, 6)
    assert resolve_deadline("by January 9", date(2026, 11, 2)) == date(2027, 1, 9)


def test_unknown_deadline_is_flagged_instead_of_guessed() -> None:
    meeting = load_meeting("01_platform_planning.json")
    items = normalize_items(meeting, RuleBasedExtractor().extract(meeting))
    investigate = next(
        item for item in items if item.subject == "investigate authentication options"
    )
    assert investigate.deadline is None
    assert "deadline_unresolved" in investigate.flags


def test_non_commitment_question_is_ignored() -> None:
    meeting = load_meeting("03_architecture_review.json")
    items = normalize_items(meeting, RuleBasedExtractor().extract(meeting))
    assert not any(item.subject == "caching" for item in items)
