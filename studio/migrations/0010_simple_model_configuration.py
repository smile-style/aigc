from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0009_storyboardshot_video_prompt_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="providerconfig",
            name="api_key_ciphertext",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="modelconfig",
            name="last_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="modelconfig",
            name="verification_latency_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="modelconfig",
            name="verification_message",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="modelconfig",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("untested", "Untested"),
                    ("success", "Verified"),
                    ("partial", "Partially verified"),
                    ("failed", "Failed"),
                ],
                default="untested",
                max_length=20,
            ),
        ),
    ]
