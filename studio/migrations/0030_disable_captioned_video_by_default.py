from django.db import migrations, models


def disable_captioned_video_by_default(apps, schema_editor):
    project_model = apps.get_model("studio", "Project")
    project_model.objects.update(workflow_generate_captioned_video=False)


class Migration(migrations.Migration):
    dependencies = [("studio", "0029_outline_is_removed_from_library")]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="workflow_generate_captioned_video",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            disable_captioned_video_by_default,
            migrations.RunPython.noop,
        ),
    ]
