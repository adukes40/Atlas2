"""What the `sync` container runs: each sync schedule set up in Settings,
plus any "Sync now" someone asks for, checked once a minute."""

import logging
import time
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reports.models import SyncRun, SyncSchedule
from reports.sync import request_sync, run_pending, sync_zone

log = logging.getLogger("atlas.sync")
SCHEDULED = ""  # requested_by for a scheduled run


def due_schedules(now=None):
    """Schedules whose time has passed today, on one of their days, and that
    have not been queued since."""
    now = (now or timezone.now()).astimezone(sync_zone())
    due = []
    for schedule in SyncSchedule.objects.filter(enabled=True):
        start = datetime.combine(now.date(), schedule.at, tzinfo=sync_zone())
        if str(now.weekday()) in schedule.days and now >= start and (not schedule.last_queued or schedule.last_queued < start):
            due.append(schedule)
    return due


def queue_due(now=None):
    for schedule in due_schedules(now):
        request_sync(SCHEDULED, schedule.platforms)
        SyncSchedule.objects.filter(pk=schedule.pk).update(last_queued=now or timezone.now())


class Command(BaseCommand):
    help = "Run the Reports sync on its schedules, and whenever someone presses Sync now."

    def handle(self, *args, **options):
        log.info("Sync schedule started; times are %s", settings.ATLAS_TIME_ZONE)
        first = True
        while True:
            try:
                if first:
                    # A run left "running" means the container stopped mid-sync.
                    SyncRun.objects.filter(state=SyncRun.RUNNING).update(
                        state=SyncRun.FAILED, finished_at=timezone.now(), progress={},
                        results={"Atlas": {"ok": False, "error": "Atlas restarted during this sync."}},
                    )
                    first = False
                queue_due()
                run = run_pending()
                if run:
                    log.info("Sync %s: %s", run.state, run.results)
            except Exception:
                # Most often the database is not ready or not migrated yet; try again next minute.
                log.exception("Sync schedule could not check for work")
            time.sleep(60)
