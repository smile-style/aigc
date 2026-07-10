from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0003_outline_script_status_per_outline_script"),
    ]

    operations = [
        migrations.CreateModel(
            name="GenerationTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("task_type", models.CharField(choices=[("outline", "Outline"), ("script", "Script"), ("storyboard", "Storyboard")], db_index=True, max_length=32)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], db_index=True, default="pending", max_length=32)),
                ("target_id", models.CharField(blank=True, default="", max_length=120)),
                ("input_snapshot", models.JSONField(blank=True, default=dict)),
                ("result_snapshot", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generation_tasks", to="studio.project")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
