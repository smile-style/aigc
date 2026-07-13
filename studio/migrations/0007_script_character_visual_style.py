from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0006_characters"),
    ]

    operations = [
        migrations.AddField(
            model_name="script",
            name="character_visual_style",
            field=models.CharField(
                choices=[("comic", "Comic"), ("realistic", "Realistic")],
                default="comic",
                max_length=20,
            ),
        ),
    ]
