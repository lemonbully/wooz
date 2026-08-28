from __future__ import annotations

from django.apps import AppConfig


class StorefrontConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "storefront"
    verbose_name = "Storefront"

    def ready(self):
        # Teach host routing which custom domains are ours: a host is a
        # storefront only when a Site claims it as a *verified* custom domain.
        # Cached briefly so this runs off Redis/locmem, not the DB, per request.
        from core.hosts import register_custom_domain_checker

        register_custom_domain_checker(_is_verified_custom_domain)


def _is_verified_custom_domain(host: str) -> bool:
    from django.core.cache import cache

    host = (host or "").lower()
    if not host:
        return False
    key = f"cd:{host}"
    value = cache.get(key)
    if value is None:
        from tenancy.models import Site

        value = Site.objects.filter(
            custom_domain=host, custom_domain_verified=True, is_deleted=False
        ).exists()
        cache.set(key, value, 120)
    return value


def invalidate_custom_domain(host: str) -> None:
    """Drop the cached verdict for a host after it is verified/removed."""
    from django.core.cache import cache

    if host:
        cache.delete(f"cd:{host.lower()}")
