from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Settings
from .digest import DigestService
from .reminders import ReminderService

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        settings: Settings,
        reminders: ReminderService,
        digests: DigestService,
    ):
        self.settings = settings
        self.reminders = reminders
        self.digests = digests
        self.scheduler = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        if not self.settings.enable_scheduler:
            logger.info("Scheduler disabled by configuration")
            return
        self.scheduler.add_job(
            self.reminders.run_due,
            "interval",
            hours=1,
            id="deadline-reminders",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self.digests.run_weekly,
            "cron",
            day_of_week=self.settings.digest_day_of_week,
            hour=self.settings.digest_hour,
            minute=self.settings.digest_minute,
            id="weekly-digest",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def run_reminders_now(self) -> list[dict]:
        return self.reminders.run_due()

    def run_digest_now(self):
        return self.digests.run_weekly()
