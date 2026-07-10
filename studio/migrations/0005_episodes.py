from django.db import migrations, models
import django.db.models.deletion


def migrate_existing_episode_data(apps, schema_editor):
    Episode = apps.get_model("studio", "Episode")
    Script = apps.get_model("studio", "Script")
    StoryboardPrompt = apps.get_model("studio", "StoryboardPrompt")

    for script in Script.objects.all():
        plan = script.plan_payload if isinstance(script.plan_payload, list) else []
        if not plan and script.episode_1_script:
            plan = [
                {
                    "episode": 1,
                    "title": "第 1 集",
                    "summary": "由旧版完整剧本迁移",
                    "key_conflict": "待补充",
                    "cliffhanger": "待补充",
                }
            ]

        for position, item in enumerate(plan, start=1):
            if not isinstance(item, dict):
                continue
            episode_number = item.get("episode", position)
            if not isinstance(episode_number, int) or isinstance(episode_number, bool):
                episode_number = position
            full_script = script.episode_1_script if episode_number == 1 else ""
            Episode.objects.update_or_create(
                script_id=script.id,
                episode_number=episode_number,
                defaults={
                    "title": str(item.get("title") or f"第 {episode_number} 集"),
                    "summary": str(item.get("summary") or "待补充"),
                    "key_conflict": str(item.get("key_conflict") or "待补充"),
                    "cliffhanger": str(item.get("cliffhanger") or "待补充"),
                    "full_script": full_script,
                    "script_status": "ready" if full_script else "pending",
                },
            )

    for storyboard in StoryboardPrompt.objects.all():
        episode = Episode.objects.filter(
            script_id=storyboard.script_id,
            episode_number=1,
        ).first()
        if episode is None:
            episode = Episode.objects.create(
                script_id=storyboard.script_id,
                episode_number=1,
                title="第 1 集",
                summary="由旧版分镜迁移",
                key_conflict="待补充",
                cliffhanger="待补充",
                full_script=storyboard.script.episode_1_script,
                script_status="ready",
            )
        storyboard.episode_id = episode.id
        storyboard.save(update_fields=["episode"])


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0004_generationtask"),
    ]

    operations = [
        migrations.CreateModel(
            name="Episode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("episode_number", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=255)),
                ("summary", models.TextField()),
                ("key_conflict", models.TextField()),
                ("cliffhanger", models.TextField()),
                ("full_script", models.TextField(blank=True)),
                ("script_status", models.CharField(choices=[("pending", "Pending"), ("generating", "Generating"), ("ready", "Ready"), ("failed", "Failed")], db_index=True, default="pending", max_length=20)),
                ("script_error", models.TextField(blank=True)),
                ("script_started_at", models.DateTimeField(blank=True, null=True)),
                ("script_finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("script", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="episodes", to="studio.script")),
            ],
            options={
                "ordering": ["episode_number"],
            },
        ),
        migrations.AddConstraint(
            model_name="episode",
            constraint=models.UniqueConstraint(fields=("script", "episode_number"), name="unique_episode_number_per_script"),
        ),
        migrations.AlterField(
            model_name="storyboardprompt",
            name="project",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storyboard_prompts", to="studio.project"),
        ),
        migrations.AddField(
            model_name="storyboardprompt",
            name="episode",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="storyboard_prompt", to="studio.episode"),
        ),
        migrations.AlterField(
            model_name="generationtask",
            name="task_type",
            field=models.CharField(choices=[("outline", "Outline"), ("script", "Script"), ("episode_script", "Episode script"), ("storyboard", "Storyboard")], db_index=True, max_length=32),
        ),
        migrations.RunPython(migrate_existing_episode_data, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="storyboardprompt",
            name="episode",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="storyboard_prompt", to="studio.episode"),
        ),
    ]
