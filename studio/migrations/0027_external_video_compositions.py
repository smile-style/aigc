from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0026_cover_template_versions")]

    operations = [
        migrations.AddField(
            model_name="videocomposition",
            name="source",
            field=models.CharField(
                choices=[
                    ("generated", "Platform generated"),
                    ("external_upload", "External upload"),
                ],
                db_index=True,
                default="generated",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="original_filename",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="video_duration_ms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="video_width",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="videocomposition",
            name="video_height",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
