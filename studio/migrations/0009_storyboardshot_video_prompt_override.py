from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0008_modelconfig_providerconfig_modelassignment_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="storyboardshot",
            name="video_prompt_override",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="storyboardshot",
            name="video_prompt_override_source_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
