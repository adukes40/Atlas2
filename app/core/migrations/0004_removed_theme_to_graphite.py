from django.db import migrations

KEPT = ("graphite", "slate")


def to_graphite(apps, schema_editor):
    """A removed theme falls back to Graphite so Settings still saves."""
    apps.get_model("core", "Organization").objects.exclude(theme__in=KEPT).update(theme="graphite")


class Migration(migrations.Migration):
    dependencies = [("core", "0003_wan_ip")]

    operations = [migrations.RunPython(to_graphite, migrations.RunPython.noop)]
