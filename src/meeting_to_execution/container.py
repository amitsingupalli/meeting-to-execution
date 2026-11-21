from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .db import Database
from .digest import DigestService, build_digest_service
from .embeddings import EmbeddingProvider, build_embedding_provider
from .extraction import HybridExtractor, build_extractor
from .graph import MeetingPipeline, build_checkpointer, build_pipeline
from .judge import HybridJudge, build_judge
from .memory import MemoryStore
from .notifications import NotificationService
from .reminders import ReminderService
from .scheduler import SchedulerService
from .tracker import Tracker, build_tracker
from .vector_index import VectorIndex, build_vector_index


@dataclass
class Container:
    settings: Settings
    db: Database
    embeddings: EmbeddingProvider
    vector: VectorIndex
    memory: MemoryStore
    extractor: HybridExtractor
    judge: HybridJudge
    tracker: Tracker
    notifier: NotificationService
    reminders: ReminderService
    digests: DigestService
    scheduler: SchedulerService
    pipeline: MeetingPipeline
    checkpoint_connection: sqlite3.Connection
    _closed: bool = False

    @classmethod
    def build(cls, settings: Settings) -> Container:
        settings.ensure_directories()
        db = Database(settings.database_url)
        db.create_all()
        embeddings = build_embedding_provider(settings)
        vector = build_vector_index(settings)
        memory = MemoryStore(db, vector, embeddings)
        extractor = build_extractor(settings)
        judge = build_judge(settings)
        tracker = build_tracker(settings, data_dir=settings.sqlite_path.parent)
        notifier = NotificationService(
            memory, settings.sqlite_path.parent / "notifications.log"
        )
        reminders = ReminderService(
            memory, notifier, hours_before=settings.reminder_hours_before
        )
        digests = build_digest_service(settings, memory, notifier)
        scheduler = SchedulerService(settings, reminders, digests)
        checkpointer, checkpoint_connection = build_checkpointer(settings.checkpoint_db)
        pipeline = build_pipeline(
            settings=settings,
            memory=memory,
            extractor=extractor,
            judge=judge,
            tracker=tracker,
            reminders=reminders,
            checkpointer=checkpointer,
        )
        return cls(
            settings=settings,
            db=db,
            embeddings=embeddings,
            vector=vector,
            memory=memory,
            extractor=extractor,
            judge=judge,
            tracker=tracker,
            notifier=notifier,
            reminders=reminders,
            digests=digests,
            scheduler=scheduler,
            pipeline=pipeline,
            checkpoint_connection=checkpoint_connection,
        )

    def start(self) -> None:
        self.scheduler.start()

    def close(self) -> None:
        if self._closed:
            return
        self.scheduler.shutdown()
        self.checkpoint_connection.close()
        self._closed = True

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "app": self.settings.app_name,
            "environment": self.settings.app_env,
            "llm_enabled": self.settings.use_llm,
            "tracker_backend": self.tracker.provider,
            "database": str(self.settings.sqlite_path),
        }
