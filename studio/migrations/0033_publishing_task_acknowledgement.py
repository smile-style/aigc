from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0032_cover_platform_masters"),
    ]

    operations = [
        migrations.AddField(
            model_name="publishingtask",
            name="acknowledged_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="publishingtask",
            name="acknowledged_by",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="publishingtask",
            name="acknowledgement_note",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddIndex(
            model_name="publishingtask",
            index=models.Index(
                fields=["status", "acknowledged_at", "finished_at"],
                name="publish_task_history_idx",
            ),
        ),
    ]
