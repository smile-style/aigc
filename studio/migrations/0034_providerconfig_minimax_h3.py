from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0033_publishing_task_acknowledgement")]

    operations = [
        migrations.AlterField(
            model_name="providerconfig",
            name="provider_type",
            field=models.CharField(
                choices=[
                    ("openai_compatible", "OpenAI compatible"),
                    ("dashscope", "Aliyun Bailian / DashScope"),
                    ("minimax_h3", "MiniMax H3 V2"),
                ],
                max_length=32,
            ),
        ),
    ]
