from __future__ import annotations

import json
import re
from typing import Protocol

from .config import Settings
from .llm import LLMClient
from .prompts import CONFLICT_SYSTEM
from .schemas import ConflictView, DecisionView, NormalizedItem


class ConflictJudge(Protocol):
    def judge(self, new: NormalizedItem, old: DecisionView) -> ConflictView: ...


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _same(left: str | None, right: str | None) -> bool:
    left_norm, right_norm = _norm(left), _norm(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    return left_norm in right_norm or right_norm in left_norm


def _date_like(value: str | None) -> bool:
    return bool(
        re.search(
            r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
            r"dec(?:ember)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"week|month|quarter|q[1-4])\b",
            _norm(value),
        )
    )


class RuleBasedJudge:
    def judge(self, new: NormalizedItem, old: DecisionView) -> ConflictView:
        same_subject = _same(new.subject, old.subject)
        same_attribute = _same(new.attribute, old.attribute)
        new_value, old_value = _norm(new.value), _norm(old.value)

        if same_subject and same_attribute:
            if new_value == old_value:
                relation, reasoning = "duplicates", (
                    f"Both records set {old.subject}/{old.attribute} to the same value."
                )
            elif _date_like(new_value) and _date_like(old_value):
                relation, reasoning = "refines", (
                    "The subject and attribute are the same, but the date/timeline changed."
                )
            elif _same(new_value, old_value):
                relation, reasoning = "refines", (
                    "The new value narrows or extends the earlier value for the same attribute."
                )
            else:
                relation, reasoning = "contradicts", (
                    f"The same {old.subject}/{old.attribute} attribute changed from "
                    f"{old.value!r} to {new.value!r}."
                )
            return ConflictView(
                new_item_id=new.id,
                old_decision_id=old.id,
                relation=relation,
                reasoning=reasoning,
                confidence=0.84,
            )

        if same_subject and not same_attribute:
            return ConflictView(
                new_item_id=new.id,
                old_decision_id=old.id,
                relation="unrelated",
                reasoning=(
                    "The records share a broad subject but describe different attributes; "
                    "similarity alone is not a conflict."
                ),
                confidence=0.76,
            )

        return ConflictView(
            new_item_id=new.id,
            old_decision_id=old.id,
            relation="unrelated",
            reasoning="The records describe different subjects or attributes.",
            confidence=0.8,
        )


class LLMJudge:
    def __init__(self, client: LLMClient):
        self.client = client

    def judge(self, new: NormalizedItem, old: DecisionView) -> ConflictView:
        payload = {
            "new": {
                "subject": new.subject,
                "attribute": new.attribute,
                "value": new.value,
                "source_quote": new.source_quote,
            },
            "old": {
                "subject": old.subject,
                "attribute": old.attribute,
                "value": old.value,
                "source_quote": old.source_quote,
            },
        }
        data = self.client.complete_json(
            system=CONFLICT_SYSTEM,
            user=json.dumps(payload, indent=2),
        )
        return ConflictView(
            new_item_id=new.id,
            old_decision_id=old.id,
            relation=data["relation"],
            reasoning=str(data.get("reasoning", "")),
            confidence=float(data.get("confidence", 0.7)),
        )


class HybridJudge:
    def __init__(self, llm: LLMJudge, fallback: RuleBasedJudge):
        self.llm = llm
        self.fallback = fallback

    def judge(self, new: NormalizedItem, old: DecisionView) -> ConflictView:
        if self.llm.client.available:
            try:
                return self.llm.judge(new, old)
            except Exception:
                pass
        return self.fallback.judge(new, old)


def build_judge(settings: Settings) -> HybridJudge:
    return HybridJudge(LLMJudge(LLMClient(settings)), RuleBasedJudge())
