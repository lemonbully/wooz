from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="theme",
            field=models.CharField(
                choices=[
                    ("template1", "Modern storefront (D2C)"),
                    ("template2", "Catalogue storefront (B2B)"),
                ],
                default="template1",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="content",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="site",
            name="allows_custom_scripts",
            field=models.BooleanField(default=False),
        ),
    ]
