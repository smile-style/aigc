# Generated for the initial MySQL-backed studio data model.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("workspace_id", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("genre", models.CharField(max_length=120)),
                ("episode_count", models.PositiveIntegerField()),
                ("episode_duration_minutes", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="Outline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outline_id", models.CharField(max_length=120)),
                ("position", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=255)),
                ("core_premise", models.TextField()),
                ("protagonist", models.TextField()),
                ("hook", models.TextField()),
                ("arc_summary", models.TextField()),
                ("raw_payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outlines", to="studio.project")),
            ],
            options={"ordering": ["position", "id"]},
        ),
        migrations.AddConstraint(
            model_name="outline",
            constraint=models.UniqueConstraint(fields=("project", "outline_id"), name="unique_outline_id_per_project"),
        ),
        migrations.AddConstraint(
            model_name="outline",
            constraint=models.UniqueConstraint(fields=("project", "position"), name="unique_outline_position_per_project"),
        ),
        migrations.CreateModel(
            name="Script",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan_payload", models.JSONField(default=list)),
                ("episode_1_script", models.TextField(blank=True)),
                ("raw_payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("outline", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scripts", to="studio.outline")),
                ("project", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="script", to="studio.project")),
            ],
        ),
        migrations.CreateModel(
            name="StoryboardPrompt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prompts_payload", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="storyboard_prompt", to="studio.project")),
                ("script", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="storyboard_prompts", to="studio.script")),
            ],
        ),
        migrations.AddField(
            model_name="project",
            name="selected_outline",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="studio.outline"),
        ),
    ]