from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0027_external_video_compositions")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="workflow_generate_captioned_video",
            field=models.BooleanField(default=True),
        ),
    ]
