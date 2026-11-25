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

        # A remembered "no such store" (storefront.resolver) must not outlive
        # the store or collaboration that takes the label.
        from django.db.models.signals import post_save

        from commerce.models import Collaboration
        from tenancy.models import Site

        post_save.connect(_forget_site_miss, sender=Site, dispatch_uid="storefront.forget_site_miss")
        post_save.connect(_forget_collab_miss, sender=Collaboration, dispatch_uid="storefront.forget_collab_miss")


def _forget_site_miss(sender, instance, **kwargs):
    from .resolver import forget_miss

    forget_miss("slug", getattr(instance, "slug", ""))


def _forget_collab_miss(sender, instance, **kwargs):
    from .resolver import forget_miss

    forget_miss("ref", getattr(instance, "subdomain", ""))


def custom_domain_candidates(host: str) -> list[str]:
    """The stored ``custom_domain`` values a request host can belong to: the
    host itself, plus its ``www`` twin  ``www.brand.com`` serves the store
    connected as ``brand.com`` (and vice versa), so an owner's ``www`` CNAME
    just works without connecting it separately."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return []
    if host.startswith("www."):
        return [host, host[4:]]
    return [host, f"www.{host}"]


def _is_verified_custom_domain(host: str) -> bool:
    from django.core.cache import cache

    candidates = custom_domain_candidates(host)
    if not candidates:
        return False
    key = f"cd:{candidates[0]}"
    value = cache.get(key)
    if value is None:
        from tenancy.models import Site

        value = Site.objects.filter(
            custom_domain__in=candidates, custom_domain_verified=True, is_deleted=False
        ).exists()
        cache.set(key, value, 120)
    return value


def invalidate_custom_domain(host: str) -> None:
    """Drop the cached verdict for a host after it is verified/removed."""
    from django.core.cache import cache

    if host:
        cache.delete(f"cd:{host.lower()}")
