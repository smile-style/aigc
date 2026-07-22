import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0013_publishing")]

    operations = [
        migrations.AddField(
            model_name="videocomposition",
            name="include_subtitles",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="subtitle_file",
            field=models.FileField(blank=True, upload_to="videos/subtitles/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="subtitle_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="SubtitleTrack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("aligning", "Aligning"), ("needs_review", "Needs review"), ("confirmed", "Confirmed"), ("failed", "Failed")], db_index=True, default="draft", max_length=24)),
                ("source_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("style_options", models.JSONField(blank=True, default=dict)),
                ("global_offset_ms", models.IntegerField(default=0)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("error_message", models.TextField(blank=True)),
                ("aligned_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("episode", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subtitle_track", to="studio.episode")),
            ],
        ),
        migrations.CreateModel(
            name="SubtitleCue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField()),
                ("source_text", models.TextField(blank=True)),
                ("recognized_text", models.TextField(blank=True)),
                ("text", models.TextField()),
                ("start_ms", models.PositiveIntegerField()),
                ("end_ms", models.PositiveIntegerField()),
                ("confidence", models.FloatField(default=0.0)),
                ("needs_review", models.BooleanField(db_index=True, default=True)),
                ("is_manually_edited", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("shot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="subtitle_cues", to="studio.storyboardshot")),
                ("track", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cues", to="studio.subtitletrack")),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [models.UniqueConstraint(fields=("track", "position"), name="unique_subtitle_cue_position")],
            },
        ),
    ]
