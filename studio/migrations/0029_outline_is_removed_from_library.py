from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0028_project_workflow_generate_captioned_video")]

    operations = [
        migrations.AddField(
            model_name="outline",
            name="is_removed_from_library",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
