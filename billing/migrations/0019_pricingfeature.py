import core.ids
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0018_invoicedetails'),
    ]

    operations = [
        migrations.CreateModel(
            name='PricingFeature',
            fields=[
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=core.ids._uuid7_fallback, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(help_text='The tab label, e.g. “Custom domain”. Keep it short.', max_length=80)),
                ('description', models.TextField(help_text='Shown under the tabs when this one is selected. Line breaks are kept.')),
                ('order', models.PositiveIntegerField(db_index=True, default=0, help_text='Tabs are shown left to right in ascending order.')),
                ('is_active', models.BooleanField(db_index=True, default=True, help_text='Only active features appear on the pricing page.')),
            ],
            options={
                'verbose_name': 'pricing feature',
                'verbose_name_plural': 'pricing features',
                'ordering': ('order', 'created_at'),
            },
        ),
    ]
