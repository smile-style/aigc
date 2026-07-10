from django.db import migrations, models
import django.db.models.deletion


def mark_existing_scripts_ready(apps, schema_editor):
    Outline = apps.get_model("studio", "Outline")
    Script = apps.get_model("studio", "Script")
    for script in Script.objects.select_related("outline"):
        Outline.objects.filter(pk=script.outline_id).update(script_status="ready")


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0002_outline_usable_archive"),
    ]

    operations = [
        migrations.AddField(
            model_name="outline",
            name="script_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="outline",
            name="script_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outline",
            name="script_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outline",
            name="script_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("generating", "Generating"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="script",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="scripts",
                to="studio.project",
            ),
        ),
        migrations.AlterField(
            model_name="script",
            name="outline",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="script",
                to="studio.outline",
            ),
        ),
        migrations.RunPython(mark_existing_scripts_ready, migrations.RunPython.noop),
    ]