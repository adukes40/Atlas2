from django.db import migrations


class Migration(migrations.Migration):
    # The test branch and main each added a 0005; this joins them.
    dependencies = [
        ("reports", "0005_person_details"),
        ("reports", "0005_sync_progress_and_schedules"),
    ]
    operations = []
