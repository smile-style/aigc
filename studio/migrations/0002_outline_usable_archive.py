from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="outline",
            name="unique_outline_position_per_project",
        ),
        migrations.AddField(
            model_name="outline",
            name="batch_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="outline",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="outline",
            name="is_usable",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="outline",
            name="usable_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="outline",
            options={"ordering": ["is_archived", "position", "id"]},
        ),
    ]