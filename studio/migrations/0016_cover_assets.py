import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0015_episode_plan_and_pacing")]

    operations = [
        migrations.CreateModel(
            name="CoverTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prompt_snapshot", models.TextField()),
                ("background", models.FileField(upload_to="covers/templates/%Y/%m/%d")),
                ("source_url", models.URLField(blank=True, max_length=1000)),
                ("model", models.CharField(max_length=120)),
                ("style_payload", models.JSONField(blank=True, default=dict)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("script", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cover_template", to="studio.script")),
            ],
        ),
        migrations.CreateModel(
            name="EpisodeCover",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=20)),
                ("image", models.FileField(upload_to="covers/episodes/%Y/%m/%d")),
                ("template_version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("episode", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cover", to="studio.episode")),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="episode_covers", to="studio.covertemplate")),
            ],
            options={"ordering": ["episode__episode_number"]},
        ),
        migrations.AlterField(
            model_name="generationtask",
            name="task_type",
            field=models.CharField(choices=[("outline", "Outline"), ("script", "Script"), ("episode_script", "Episode script"), ("storyboard", "Storyboard"), ("character_profile", "Character profile"), ("character_image", "Character image"), ("cover_image", "Cover image")], db_index=True, max_length=32),
        ),
    ]
