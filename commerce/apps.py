from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate


class CommerceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commerce"
    verbose_name = "Commerce"

    def ready(self):
        # Price history: every Product save appends a ProductPriceChange when the
        # selling price (base or any variation) actually changed.
        from django.db.models.signals import post_save, pre_save

        from . import price_history
        from .models import Product

        pre_save.connect(
            price_history.on_product_pre_save, sender=Product,
            dispatch_uid="commerce.price_history.on_product_pre_save",
        )
        post_save.connect(
            price_history.on_product_saved, sender=Product,
            dispatch_uid="commerce.price_history.on_product_saved",
        )

        # Google Merchant auto-sync: a saved product (created, edited,
        # deactivated or soft-deleted) is queued for Google after commit; a
        # hard-deleted one has its offers taken down. No-ops for stores that
        # aren't connected with auto-sync on.
        from django.db.models.signals import pre_delete

        post_save.connect(
            _merchant_product_saved, sender=Product,
            dispatch_uid="commerce.google_merchant.product_saved",
        )
        pre_delete.connect(
            _merchant_product_deleting, sender=Product,
            dispatch_uid="commerce.google_merchant.product_deleting",
        )

        # On localhost, seed the demo store right after migrations so the
        # dashboard has something to show without any manual step. Scoped to
        # this app's post_migrate so it fires once per `migrate`  and only
        # after commerce (and its tenancy/accounts dependencies) are applied.
        # The seed itself is idempotent and no-ops outside DEBUG.
        if getattr(settings, "DEV_SEED_STORE", False):
            post_migrate.connect(_seed_dev_store, sender=self)


def _merchant_product_saved(sender, instance, raw=False, **kwargs):
    if raw:  # fixtures / loaddata
        return
    from . import google_merchant

    google_merchant.queue_product_sync(instance)


def _merchant_product_deleting(sender, instance, **kwargs):
    from . import google_merchant

    google_merchant.queue_offer_removal(instance)


def _seed_dev_store(sender, **kwargs):
    from core.dev_seed import seed_dev_store

    try:
        seed_dev_store()
    except Exception:
        # A seed failure must never break `migrate`. It is a dev convenience;
        # if it cannot run (e.g. a half-applied schema), the developer can call
        # `manage.py seed_dev_store` once migrations are complete.
        pass
