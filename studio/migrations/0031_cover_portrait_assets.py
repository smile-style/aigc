from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0030_disable_captioned_video_by_default")]

    operations = [
        migrations.AddField(
            model_name="covertemplate",
            name="portrait_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="covertemplateversion",
            name="portrait_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="episodecover",
            name="portrait_image",
            field=models.FileField(blank=True, upload_to="covers/episodes/%Y/%m/%d"),
        ),
    ]
