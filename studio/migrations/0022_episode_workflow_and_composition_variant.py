from django.db import migrations, models
import django.db.models.deletion


def set_existing_composition_variants(apps, schema_editor):
    VideoComposition = apps.get_model("studio", "VideoComposition")
    VideoComposition.objects.filter(include_subtitles=True).update(variant="captioned")


class Migration(migrations.Migration):
    dependencies = [("studio", "0021_character_soft_delete")]

    operations = [
        migrations.AddField(
            model_name="videocomposition",
            name="variant",
            field=models.CharField(
                choices=[("clean", "Without subtitles"), ("captioned", "With subtitles")],
                db_index=True,
                default="clean",
                max_length=16,
            ),
        ),
        migrations.RunPython(set_existing_composition_variants, migrations.RunPython.noop),
        migrations.CreateModel(
            name="EpisodeWorkflowRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], db_index=True, default="queued", max_length=24)),
                ("stage", models.CharField(choices=[("script", "Episode script"), ("characters", "Characters"), ("storyboard", "Storyboard"), ("videos", "Shot videos"), ("clean_export", "Clean export"), ("subtitles", "Subtitles"), ("captioned_export", "Captioned export"), ("complete", "Complete")], db_index=True, default="script", max_length=32)),
                ("progress_percent", models.PositiveSmallIntegerField(default=0)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("child_task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="workflow_steps", to="studio.generationtask")),
                ("episode", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workflow_runs", to="studio.episode")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["status", "stage", "created_at"], name="episode_workflow_queue_idx")],
            },
        ),
    ]
