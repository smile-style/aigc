from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("studio", "0018_shot_subtitle_settings")]

    operations = [
        migrations.RemoveField(
            model_name="shotsubtitlesetting",
            name="style_options",
        ),
    ]
