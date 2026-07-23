import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0017_episodecover_title_customized")]

    operations = [
        migrations.AddField(
            model_name="subtitlecue",
            name="local_end_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subtitlecue",
            name="local_start_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ShotSubtitleSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("aligning", "Aligning"),
                            ("needs_review", "Needs review"),
                            ("confirmed", "Confirmed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=24,
                    ),
                ),
                ("source_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("style_options", models.JSONField(blank=True, default=dict)),
                ("offset_ms", models.IntegerField(default=0)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("error_message", models.TextField(blank=True)),
                ("aligned_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "shot",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subtitle_setting",
                        to="studio.storyboardshot",
                    ),
                ),
            ],
        ),
    ]
