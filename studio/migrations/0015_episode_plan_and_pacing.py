from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0014_subtitles")]

    operations = [
        migrations.AddField(
            model_name="episode",
            name="plan_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="episode",
            name="pacing_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
