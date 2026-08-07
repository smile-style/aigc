import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0034_providerconfig_minimax_h3")]

    operations = [
        migrations.AddField(
            model_name="episode",
            name="continuity_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="episode",
            name="continuity_input_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="episode",
            name="is_story_stale",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="storyboardprompt",
            name="cold_open_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="edit_plan",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="LLMRequestRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("purpose", models.CharField(db_index=True, max_length=64)),
                (
                    "target_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        max_length=120,
                    ),
                ),
                (
                    "episode_number",
                    models.PositiveIntegerField(blank=True, db_index=True, null=True),
                ),
                ("task_attempt", models.PositiveIntegerField(default=1)),
                ("call_sequence", models.PositiveIntegerField(default=1)),
                ("model", models.CharField(max_length=120)),
                ("temperature", models.FloatField(blank=True, null=True)),
                ("sanitized_payload", models.JSONField(default=dict)),
                ("payload_hash", models.CharField(db_index=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="llm_request_records",
                        to="studio.project",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="llm_request_records",
                        to="studio.generationtask",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["project", "purpose", "episode_number", "-created_at"],
                        name="llm_req_project_lookup_idx",
                    ),
                    models.Index(
                        fields=["task", "task_attempt", "call_sequence"],
                        name="llm_req_task_history_idx",
                    ),
                ],
            },
        ),
    ]
