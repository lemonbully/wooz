import core.ids
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('commerce', '0011_shippingprovider_shiprocket_credentials'),
    ]

    operations = [
        migrations.CreateModel(
            name='TrustBadge',
            fields=[
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True, editable=False)),
                ('id', models.UUIDField(default=core.ids._uuid7_fallback, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('image', models.FileField(blank=True, help_text='Icon shown with the badge. PNG, JPG, WEBP or SVG.', null=True, upload_to='trust_badges/')),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'trust badge',
                'verbose_name_plural': 'trust badges',
                'ordering': ('order', 'name'),
            },
        ),
    ]
