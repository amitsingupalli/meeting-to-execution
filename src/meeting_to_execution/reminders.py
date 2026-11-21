from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from .memory import MemoryStore
from .notifications import NotificationService
from .schemas import ReminderView, TaskDraft, TaskView


class ReminderService:
    def __init__(
        self,
        memory: MemoryStore,
        notifier: NotificationService,
        hours_before: int = 48,
    ):
        self.memory = memory
        self.notifier = notifier
        self.hours_before = hours_before

    def schedule_tasks(self, tasks: list[TaskDraft]) -> list[ReminderView]:
        reminders: list[ReminderView] = []
        now = datetime.now(UTC)
        for task in tasks:
            if not task.owner_id or not task.deadline:
                continue
            scheduled = datetime.combine(task.deadline, time(9, 0), tzinfo=UTC) - timedelta(
                hours=self.hours_before
            )
            if scheduled < now:
                scheduled = now + timedelta(minutes=1)
            owner_name = self.memory.owner_name(task.owner_id) or task.owner_id
            message = (
                f"Reminder for {owner_name}: '{task.title}' is due on "
                f"{task.deadline.isoformat()}."
            )
            reminders.append(
                self.memory.create_reminder(
                    task_id=task.id,
                    scheduled_for=scheduled,
                    message=message,
                )
            )
        return reminders

    def run_due(self, now: datetime | None = None) -> list[dict]:
        current = now or datetime.now(UTC)
        sent: list[dict] = []
        for reminder in self.memory.pending_reminders(current):
            task = self.memory.get_task(reminder.task_id)
            if not task or not task.owner_id:
                continue
            self.notifier.send(
                kind="reminder",
                recipient=task.owner_id,
                subject=f"Deadline reminder: {task.title}",
                body=reminder.message,
            )
            self.memory.mark_reminder_sent(reminder.id)
            sent.append(
                {
                    "reminder_id": reminder.id,
                    "task_id": task.id,
                    "owner_id": task.owner_id,
                }
            )
        return sent

    def schedule_task_views(self, tasks: list[TaskView]) -> list[ReminderView]:
        drafts = [
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
        ]
        return self.schedule_tasks(drafts)
