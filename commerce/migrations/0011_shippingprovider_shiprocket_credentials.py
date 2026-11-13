"""Shiprocket API credentials + cached token on ShippingProvider.

Adds the fields the live Shiprocket client needs: the API user email/password
(encrypted at rest), the cached bearer token and its expiry, the pickup-location
nickname that create/adhoc must match, and an optional API base override.
"""

from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0010_remove_coupon_uniq_coupon_code_per_site_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="shippingprovider",
            name="api_email",
            field=core.fields.EncryptedCharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="shippingprovider",
            name="api_password",
            field=core.fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="shippingprovider",
            name="access_token",
            field=core.fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="shippingprovider",
            name="access_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shippingprovider",
            name="pickup_location",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="shippingprovider",
            name="base_url",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
