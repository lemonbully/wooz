"""Idempotent demo data for local development.

On localhost we want a working store to exist the moment the database is ready 
so the dashboard, media library, category and product pages have something to
show without anyone hand-creating a store first. This seeds exactly that: one
demo store, owned by a demo account, classified under a demo business
category/subcategory, and carrying a demo product category with a demo
subcategory nested under it.

Two guarantees make it safe to call from anywhere (a management command, a
``post_migrate`` hook, a shell):

* **Never in production.** It no-ops unless ``settings.DEBUG`` is true, so a
  live deployment can never grow a demo store or a demo login.
* **Idempotent.** Every object is fetched-or-created by a stable natural key, so
  running it a hundred times leaves exactly one of each.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

__all__ = ["seed_dev_store", "DEMO_OWNER_EMAIL", "DEMO_OWNER_PASSWORD", "DEMO_STORE_SLUG"]

# Stable natural keys  the seed finds existing rows by these, so re-running it
# updates nothing and duplicates nothing.
DEMO_OWNER_EMAIL = "demo@onewooz.local"
DEMO_OWNER_PASSWORD = "demo-store-local-pass"  # ≥12 chars; local only
DEMO_STORE_SLUG = "demo-store"


def seed_dev_store(*, log=None):
    """Ensure the local demo store (and its demo category/subcategory) exist.

    Returns the ``Site`` (or ``None`` when skipped outside DEBUG). ``log`` is an
    optional callable  e.g. ``self.stdout.write``  used to report what was
    created.
    """
    if not settings.DEBUG:
        return None

    # Imported lazily so importing this module never drags the ORM in at the
    # wrong time (e.g. during app loading before the registry is ready).
    from django.contrib.auth import get_user_model

    from commerce.models import Category
    from tenancy.models import BusinessCategory, BusinessSubCategory, Site

    User = get_user_model()

    def say(msg):
        if log:
            log(msg)

    # -- owner ------------------------------------------------------------
    owner, created = User.objects.get_or_create(
        email=DEMO_OWNER_EMAIL,
        defaults={"full_name": "Demo Owner", "email_verified_at": timezone.now()},
    )
    if created or not owner.has_usable_password():
        owner.set_password(DEMO_OWNER_PASSWORD)
        if owner.email_verified_at is None:
            owner.email_verified_at = timezone.now()
        owner.save(update_fields=["password", "password_changed_at",
                                  "email_verified_at", "updated_at"])
    say(f"owner: {owner.email} ({'created' if created else 'exists'})")

    # -- platform business taxonomy --------------------------------------
    biz_cat, _ = BusinessCategory.objects.get_or_create(name="Demo Category")
    biz_sub, _ = BusinessSubCategory.objects.get_or_create(
        category=biz_cat, name="Demo Subcategory"
    )

    # -- the store --------------------------------------------------------
    site, created = Site.objects.get_or_create(
        slug=DEMO_STORE_SLUG,
        defaults={
            "owner": owner,
            "name": "Demo Store",
            "business_model": Site.BusinessModel.D2C,
            "theme": Site.Theme.TEMPLATE2,
            "business_category": biz_cat,
            "business_subcategory": biz_sub,
            "status": Site.Status.LIVE,
            "tagline": "A demo storefront for local development.",
        },
    )
    say(f"store: {site.name} /s/{site.slug}/ ({'created' if created else 'exists'})")

    # -- product category + nested subcategory (tenant-scoped) -----------
    # `all_objects` bypasses the tenant guard that `objects` enforces; safe here
    # because we pass `site` explicitly on every call.
    category, _ = Category.all_objects.get_or_create(
        site=site,
        slug="demo-category",
        defaults={"name": "Demo Category", "is_active": True, "show_on_homepage": True},
    )
    Category.all_objects.get_or_create(
        site=site,
        slug="demo-subcategory",
        defaults={"name": "Demo Subcategory", "parent": category, "is_active": True},
    )
    say("category: Demo Category → Demo Subcategory")

    return site
