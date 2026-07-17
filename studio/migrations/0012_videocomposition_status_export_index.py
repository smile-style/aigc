from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0011_alter_videocomposition_options_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="videocomposition",
            index=models.Index(
                fields=["status", "-exported_at"],
                name="video_comp_status_export_idx",
            ),
        ),
    ]
