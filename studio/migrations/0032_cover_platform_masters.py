from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0031_cover_portrait_assets")]

    operations = [
        migrations.AddField(
            model_name="covertemplate",
            name="xiaohongshu_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="covertemplate",
            name="douyin_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="covertemplateversion",
            name="xiaohongshu_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
        migrations.AddField(
            model_name="covertemplateversion",
            name="douyin_background",
            field=models.FileField(blank=True, upload_to="covers/templates/%Y/%m/%d"),
        ),
    ]
