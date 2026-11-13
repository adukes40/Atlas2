from django.core.management.base import BaseCommand

from reports.models import SyncRun
from reports.sync import execute


class Command(BaseCommand):
    help = "Copy Incident IQ, Google and Mist into Atlas now, for Reports, and show what happened."

    def handle(self, *args, **options):
        run = execute(SyncRun.objects.create(requested_by="command line"))
        for name, result in run.results.items():
            self.stdout.write(f"{name}: {result.get('detail') or result.get('error', '')}")
        self.stdout.write(f"Sync {'finished' if run.state == SyncRun.DONE else 'failed'}.")
