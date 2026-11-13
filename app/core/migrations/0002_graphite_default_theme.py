from django.db import migrations, models

import core.themes


def rename_default_theme(apps, schema_editor):
    """"default" was the first theme's key. Graphite is the default look now."""
    Organization = apps.get_model("core", "Organization")
    Organization.objects.filter(theme="default").update(theme="graphite")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="organization",
            name="theme",
            field=models.CharField(
                choices=core.themes.theme_choices, default="graphite", max_length=40
            ),
        ),
        migrations.RunPython(rename_default_theme, migrations.RunPython.noop),
    ]
