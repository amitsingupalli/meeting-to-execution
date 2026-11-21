from __future__ import annotations

from datetime import UTC, date, datetime

from meeting_to_execution.judge import RuleBasedJudge
from meeting_to_execution.schemas import DecisionView, NormalizedItem


def make_item(subject: str, attribute: str | None, value: str | None) -> NormalizedItem:
    return NormalizedItem(
        id="new",
        type="decision",
        subject=subject,
        attribute=attribute,
        value=value,
        confidence=0.9,
        source_quote="quote",
    )


def make_old(subject: str, attribute: str | None, value: str | None) -> DecisionView:
    return DecisionView(
        id="old",
        type="decision",
        subject=subject,
        attribute=attribute,
        value=value,
        confidence=0.9,
        source_quote="quote",
        status="active",
        meeting_id="mtg_old",
        meeting_date=date(2026, 11, 2),
        created_at=datetime.now(UTC),
    )


def judge(new: NormalizedItem, old: DecisionView) -> str:
    return RuleBasedJudge().judge(new, old).relation


def test_changed_value_for_same_attribute_contradicts() -> None:
    assert (
        judge(
            make_item("database", "primary database", "MySQL"),
            make_old("database", "primary database", "Postgres"),
        )
        == "contradicts"
    )
    assert (
        judge(
            make_item("caching", "cache provider", "the database-backed cache"),
            make_old("caching", "cache provider", "Redis"),
        )
        == "contradicts"
    )


def test_changed_date_is_a_refinement_not_a_contradiction() -> None:
    assert (
        judge(
            make_item("beta launch", "launch date", "December 18"),
            make_old("beta launch", "launch date", "December 4"),
        )
        == "refines"
    )


def test_identical_decision_is_a_duplicate() -> None:
    assert (
        judge(
            make_item("beta launch", "launch date", "December 4"),
            make_old("beta launch", "launch date", "December 4"),
        )
        == "duplicates"
    )


def test_same_subject_different_attribute_is_not_a_conflict() -> None:
    verdict = RuleBasedJudge().judge(
        make_item("deployment", "region", "eu-west-1"),
        make_old("deployment", "cloud provider", "AWS"),
    )
    assert verdict.relation == "unrelated"
    assert "similarity alone is not a conflict" in verdict.reasoning


def test_different_subjects_are_unrelated() -> None:
    assert (
        judge(
            make_item("authentication", "auth provider", "Auth0"),
            make_old("database", "primary database", "Postgres"),
        )
        == "unrelated"
    )


def test_judge_is_deterministic() -> None:
    new = make_item("database", "primary database", "MySQL")
    old = make_old("database", "primary database", "Postgres")
    first = RuleBasedJudge().judge(new, old)
    second = RuleBasedJudge().judge(new, old)
    assert first.model_dump() == second.model_dump()
