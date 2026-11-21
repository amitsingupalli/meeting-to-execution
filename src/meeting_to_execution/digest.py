from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from .config import Settings
from .llm import LLMClient
from .memory import MemoryStore
from .notifications import NotificationService
from .prompts import DIGEST_SYSTEM
from .schemas import DigestView, TaskView


def week_bounds(reference: date | None = None) -> tuple[date, date]:
    today = reference or datetime.now(UTC).date()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


class DigestService:
    def __init__(
        self,
        memory: MemoryStore,
        notifier: NotificationService,
        llm: LLMClient,
    ):
        self.memory = memory
        self.notifier = notifier
        self.llm = llm

    def _matching_tasks(
        self, tasks: list[TaskView], start: date, end: date
    ) -> list[TaskView]:
        matches = []
        for task in tasks:
            if task.status not in {"approved", "posted"}:
                continue
            created = task.created_at.date()
            if (task.deadline and start <= task.deadline <= end) or (
                start <= created <= end
            ):
                matches.append(task)
        return matches

    def _deterministic_text(
        self,
        owner_name: str,
        start: date,
        end: date,
        tasks: list[TaskView],
    ) -> str:
        due = [t for t in tasks if t.deadline and start <= t.deadline <= end]
        upcoming = [t for t in tasks if t.deadline and t.deadline > end]
        posted = [t for t in tasks if t.status == "posted"]

        lines = [
            f"Hi {owner_name}, here is your execution digest for "
            f"{start.isoformat()} to {end.isoformat()}.",
            "",
            "Due this week:",
        ]
        if due:
            for task in due:
                lines.append(f"- {task.title} (due {task.deadline}, {task.status})")
        else:
            lines.append("- Nothing due this week.")

        lines.extend(["", "Coming up:"])
        if upcoming:
            for task in upcoming:
                lines.append(f"- {task.title} (due {task.deadline}, {task.status})")
        else:
            lines.append("- No upcoming deadlines on file.")

        lines.extend(["", "Recently posted:"])
        if posted:
            for task in posted[:8]:
                ref = task.tracker_ref or "tracker pending"
                lines.append(f"- {task.title} ({ref})")
        else:
            lines.append("- No posted tickets yet.")

        return "\n".join(lines)

    def _llm_text(
        self,
        owner_name: str,
        start: date,
        end: date,
        tasks: list[TaskView],
    ) -> str | None:
        if not self.llm.available:
            return None
        payload = {
            "owner": owner_name,
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "tasks": [
                {
                    "title": task.title,
                    "deadline": task.deadline.isoformat() if task.deadline else None,
                    "status": task.status,
                    "tracker_ref": task.tracker_ref,
                }
                for task in tasks
            ],
        }
        try:
            return self.llm.complete_text(
                system=DIGEST_SYSTEM,
                user=json.dumps(payload, indent=2),
            )
        except Exception:
            return None

    def build_for_owner(
        self,
        owner_id: str,
        start: date,
        end: date,
        *,
        save: bool = True,
    ) -> DigestView | None:
        tasks = [
            task
            for task in self.memory.list_tasks(owner_id=owner_id, limit=500)
            if task.status in {"approved", "posted"}
            and (
                (task.deadline and start <= task.deadline <= end)
                or start <= task.created_at.date() <= end
            )
        ]
        if not tasks:
            return None

        owner_name = self.memory.owner_name(owner_id) or owner_id
        text = self._llm_text(owner_name, start, end, tasks) or self._deterministic_text(
            owner_name, start, end, tasks
        )
        if not save:
            return DigestView(
                id=0,
                owner_id=owner_id,
                owner_name=owner_name,
                week_start=start,
                week_end=end,
                text=text,
                created_at=datetime.now(UTC),
            )
        return self.memory.save_digest(owner_id, start, end, text)

    def run_weekly(
        self,
        start: date | None = None,
        end: date | None = None,
        *,
        notify: bool = True,
    ) -> list[DigestView]:
        week_start, week_end = (
            (start, end) if start and end else week_bounds()
        )
        tasks = self.memory.list_tasks(limit=1000)
        owners = sorted(
            {
                task.owner_id
                for task in self._matching_tasks(tasks, week_start, week_end)
                if task.owner_id
            }
        )
        digests: list[DigestView] = []
        for owner_id in owners:
            digest = self.build_for_owner(owner_id, week_start, week_end)
            if not digest:
                continue
            digests.append(digest)
            if notify:
                self.notifier.send(
                    kind="digest",
                    recipient=owner_id,
                    subject=(
                        f"Weekly execution digest: {week_start.isoformat()} "
                        f"to {week_end.isoformat()}"
                    ),
                    body=digest.text,
                )
                self.memory.mark_digest_sent(digest.id)
        return digests


def build_digest_service(
    settings: Settings, memory: MemoryStore, notifier: NotificationService
) -> DigestService:
    return DigestService(memory, notifier, LLMClient(settings))
