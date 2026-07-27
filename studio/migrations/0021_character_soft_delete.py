from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0020_reliable_generation_tasks"),
    ]

    operations = [
        migrations.AddField(
            model_name="character",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="character",
            name="is_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
