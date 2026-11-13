import core.ids
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0012_planfeature_value'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountEntitlement',
            fields=[
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True, editable=False)),
                ('id', models.UUIDField(default=core.ids._uuid7_fallback, editable=False, primary_key=True, serialize=False)),
                ('email', models.EmailField(db_index=True, help_text='The owner this grant belongs to (matched case-insensitively).', max_length=254, unique=True)),
                ('plan_type', models.CharField(choices=[('free', 'Free'), ('paid', 'Paid (recurring)'), ('one_time', 'One-time (one year, paid once)'), ('custom', 'Custom (talk to us)')], default='free', max_length=10)),
                ('plan_name', models.CharField(blank=True, default='', max_length=120)),
                ('number_of_stores', models.PositiveIntegerField(default=1)),
                ('number_of_collaborations', models.PositiveIntegerField(default=0)),
                ('included_features', models.JSONField(blank=True, default=list)),
                ('included_editor_tabs', models.JSONField(blank=True, default=list)),
                ('commission', models.JSONField(blank=True, default=list)),
                ('penalties', models.JSONField(blank=True, default=list)),
                ('schema_version', models.PositiveSmallIntegerField(default=1)),
                ('granted_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('source_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='billing.subscriptionorder')),
                ('source_plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='billing.subscriptionplan')),
            ],
            options={
                'verbose_name': 'account entitlement',
                'verbose_name_plural': 'account entitlements',
                'ordering': ('-granted_at',),
            },
        ),
    ]
