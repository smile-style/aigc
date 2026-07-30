from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0024_subtitle_qc")]

    operations = [
        migrations.AddField(
            model_name="publishingloginsession",
            name="callback_state_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name="publishingaccount",
            name="platform",
            field=models.CharField(
                choices=[
                    ("bilibili", "Bilibili"),
                    ("acfun", "AcFun"),
                    ("douyin", "抖音"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="publishingloginsession",
            name="platform",
            field=models.CharField(
                choices=[
                    ("bilibili", "Bilibili"),
                    ("acfun", "AcFun"),
                    ("douyin", "抖音"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="publishingtask",
            name="platform",
            field=models.CharField(
                choices=[
                    ("bilibili", "Bilibili"),
                    ("acfun", "AcFun"),
                    ("douyin", "抖音"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
