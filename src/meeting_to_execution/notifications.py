from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .memory import MemoryStore
from .schemas import NotificationView

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, memory: MemoryStore, log_path: str | Path = "./data/notifications.log"):
        self.memory = memory
        self.log_path = Path(log_path).expanduser().resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, *, kind: str, recipient: str, subject: str, body: str) -> NotificationView:
        notification = self.memory.save_notification(
            kind=kind,
            recipient=recipient,
            subject=subject,
            body=body,
            channel="log",
            status="sent",
        )
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n[{timestamp}] {kind} -> {recipient}\n"
                f"Subject: {subject}\n{body}\n"
            )
        logger.info("%s notification sent to %s: %s", kind, recipient, subject)
        return notification
