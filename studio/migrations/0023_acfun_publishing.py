from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0022_episode_workflow_and_composition_variant")]

    operations = [
        migrations.AlterField(
            model_name="publishingaccount",
            name="platform",
            field=models.CharField(
                choices=[("bilibili", "Bilibili"), ("acfun", "AcFun")],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="publishingloginsession",
            name="platform",
            field=models.CharField(
                choices=[("bilibili", "Bilibili"), ("acfun", "AcFun")], max_length=32
            ),
        ),
        migrations.AlterField(
            model_name="publishingtask",
            name="platform",
            field=models.CharField(
                choices=[("bilibili", "Bilibili"), ("acfun", "AcFun")],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
