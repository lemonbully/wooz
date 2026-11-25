from django.apps import AppConfig


class MarketplaceConfig(AppConfig):
    """OneWooz Apps: the marketplace on marketplace.onewooz.com.

    See MARKETPLACE_PLAN.md at the repository root for the design. The short
    version: an App is configuration composed in the Django admin from building
    blocks that live in code (:mod:`marketplace.blocks`, :mod:`marketplace.modules`).
    Storefronts never read marketplace tables; they read the compiled
    ``SiteAppRender`` row and ``Site.theme`` only.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "marketplace"
    verbose_name = "Marketplace (OneWooz Apps)"

    def ready(self):
        from django.conf import settings
        from django.db.models.signals import post_migrate

        from . import checks  # noqa: F401  (registers the isolation system checks)
        from . import signals  # noqa: F401

        # Localhost only: the system app (OneWooz Templates) and three example
        # apps exist right after `migrate`, same switch as the demo store.
        if settings.DEBUG and getattr(settings, "DEV_SEED_STORE", False):
            post_migrate.connect(_seed_examples, sender=self, dispatch_uid="marketplace_dev_seed")


def _seed_examples(sender, **kwargs):
    try:
        from .dev_seed import seed

        seed()
    except Exception:
        # Tables not migrated yet in this run, or anything else: never block migrate.
        pass
