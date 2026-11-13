"""What the `sync` container runs: the nightly sync at ATLAS_SYNC_HOUR, plus
any "Sync now" someone asks for, checked once a minute."""

import logging
import time
from datetime import datetime, time as clock

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reports.models import SyncRun
from reports.sync import request_sync, run_pending, sync_zone

log = logging.getLogger("atlas.sync")
NIGHTLY = ""  # requested_by for the scheduled run


def nightly_due(now=None):
    """True once today's sync hour has passed and today's nightly run has not been queued."""
    now = (now or timezone.now()).astimezone(sync_zone())
    start = datetime.combine(now.date(), clock(settings.ATLAS_SYNC_HOUR), tzinfo=sync_zone())
    return now >= start and not SyncRun.objects.filter(requested_by=NIGHTLY, created_at__gte=start).exists()


class Command(BaseCommand):
    help = "Run the Reports sync every night, and whenever someone presses Sync now."

    def handle(self, *args, **options):
        log.info("Sync schedule started: every day at %02d:00 %s", settings.ATLAS_SYNC_HOUR, settings.ATLAS_TIME_ZONE)
        first = True
        while True:
            try:
                if first:
                    # A run left "running" means the container stopped mid-sync.
                    SyncRun.objects.filter(state=SyncRun.RUNNING).update(
                        state=SyncRun.FAILED, finished_at=timezone.now(),
                        results={"Atlas": {"ok": False, "error": "Atlas restarted during this sync."}},
                    )
                    first = False
                if nightly_due() and not SyncRun.objects.filter(state=SyncRun.REQUESTED).exists():
                    request_sync(NIGHTLY)
                run = run_pending()
                if run:
                    log.info("Sync %s: %s", run.state, run.results)
            except Exception:
                # Most often the database is not ready or not migrated yet; try again next minute.
                log.exception("Sync schedule could not check for work")
            time.sleep(60)
