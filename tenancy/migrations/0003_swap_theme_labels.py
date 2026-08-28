from django.db import migrations, models


def _swap_themes(apps, schema_editor):
    """Interchange the two designs on existing stores.

    Stores created under the old mapping have template1 for D2C and template2
    for B2B. Swapping the stored values flips each store to the corrected
    design (template1 = B2B, template2 = D2C).
    """
    Site = apps.get_model("tenancy", "Site")
    Site.objects.filter(theme="template1").update(theme="__swap_tmp__")
    Site.objects.filter(theme="template2").update(theme="template1")
    Site.objects.filter(theme="__swap_tmp__").update(theme="template2")


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0002_site_content_theme"),
    ]

    operations = [
        migrations.AlterField(
            model_name="site",
            name="theme",
            field=models.CharField(
                choices=[
                    ("template1", "Catalogue storefront (B2B)"),
                    ("template2", "Modern storefront (D2C)"),
                ],
                default="template1",
                max_length=16,
            ),
        ),
        migrations.RunPython(_swap_themes, _swap_themes),
    ]
