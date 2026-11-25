"""The owner's design library (OneWooz Templates) and the automatic backfill.

Library rule (Settings -> Storefront design and the create-store form):
Template 1 (the system default, everyone has it) + every design with an active
licence + the design the store is on right now. Nothing else.

:func:`ensure` migrates existing owners with **no manual step**. It runs lazily
on the first dashboard / settings load after deploy (idempotent, cached) and
from a beat sweep for owners who never log in. It creates:

* a ``grandfathered`` licence, for that one store, for the design each store is
  on today when that is not Template 1 (a live store is never switched away);
* a ``purchase`` licence for every paid, unexpired legacy ``TemplatePurchase``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Q

from . import licences
from .licences import Owner, owner_of

logger = logging.getLogger("onewooz.marketplace")

_ENSURE_VERSION = 2


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------
def ensure(owner_or_user) -> None:
    owner = owner_of(owner_or_user)
    if owner is None:
        return
    key = f"mkt:ensured:{_ENSURE_VERSION}:{owner.ref}"
    if cache.get(key):
        return
    try:
        _ensure(owner)
        cache.set(key, 1, 60 * 60 * 24)
    except Exception:
        logger.warning("library.ensure failed for %s", owner.email, exc_info=True)


def _ensure(owner: Owner) -> None:
    from tenancy.models import Site

    from .models import AppItem, Licence
    from .registry import templates_app

    app = templates_app()
    if app is None:
        return
    items = {i.key: i for i in AppItem.objects.filter(app=app)}

    # 1. Grandfather each store's current design (that store only).
    for site in Site.objects.filter(owner_id=owner.ref, is_deleted=False).defer("content"):
        key = site.theme_choice
        item = items.get(key)
        if item is None or item.is_system_default:
            continue
        # Covered already: an owner-wide licence, or one for this very store.
        if Licence.objects.filter(owner_ref=owner.ref, item=item).filter(
            licences._current_q()
        ).filter(Q(site_ref__isnull=True) | Q(site_ref=site.id)).exists():
            continue
        licences.grant(
            owner, app, item=item, source=Licence.Source.GRANDFATHERED, site_ref=site.id,
            price=Decimal("0"), reason="Design in use before the marketplace",
        )

    # 2. Legacy purchases (billing.TemplatePurchase) become purchase licences.
    try:
        from billing.models import TemplatePurchase
    except Exception:
        return
    if not owner.email:
        return
    for p in TemplatePurchase.objects.filter(email__iexact=owner.email, status=TemplatePurchase.Status.PAID):
        item = items.get(p.template_key)
        if item is None:
            continue
        if p.expires_at is not None and not p.is_current:
            continue
        licences.grant(
            owner, app, item=item, source=Licence.Source.PURCHASE, expires_at=p.expires_at,
            legacy_ref=f"OWT:{p.number}", price=p.base_amount, reason="Imported legacy template purchase",
        )


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------
@dataclass
class DesignEntry:
    item: object
    key: str
    licence: object | None
    in_library: bool
    is_current: bool
    lapsed: bool


def _items(app):
    from .models import AppItem

    return list(AppItem.objects.filter(app=app).order_by("order", "name"))


def entries_for_site(site) -> list[DesignEntry] | None:
    """Every design with this store's library state. ``None`` before migration."""
    from .models import Licence
    from .registry import templates_app

    app = templates_app()
    if app is None:
        return None
    owner = owner_of(site)
    ensure(owner)
    lic_by_key: dict[str, object] = {}
    for lic in licences.active_licences(owner, app=app):
        if not lic.item_key:
            continue
        if lic.site_ref and str(lic.site_ref) != str(site.id):
            continue
        prev = lic_by_key.get(lic.item_key)
        if prev is None or (prev.expires_at is not None and (lic.expires_at is None or lic.expires_at > prev.expires_at)):
            lic_by_key[lic.item_key] = lic
    lapsed_keys = set()
    if owner is not None:
        lapsed_keys = set(
            Licence.objects.filter(owner_ref=owner.ref, app=app, status=Licence.Status.EXPIRED)
            .values_list("item_key", flat=True)[:50]
        )
    current = site.theme_choice
    out = []
    for item in _items(app):
        lic = lic_by_key.get(item.key)
        in_lib = item.is_system_default or lic is not None or item.key == current
        out.append(
            DesignEntry(
                item=item, key=item.key, licence=lic, in_library=in_lib,
                is_current=item.key == current,
                lapsed=(lic is None and item.key in lapsed_keys),
            )
        )
    return out


def library_keys_for_owner(owner_or_user) -> set[str] | None:
    """Design keys this owner may use on a *new* store (create-store form)."""
    from .registry import templates_app

    app = templates_app()
    if app is None:
        return None
    owner = owner_of(owner_or_user)
    ensure(owner)
    keys = {i.key for i in _items(app) if i.is_system_default}
    for lic in licences.active_licences(owner, app=app):
        if lic.item_key and not lic.site_ref:
            keys.add(lic.item_key)
    return keys


def can_use_design(site, key: str) -> bool | None:
    entries = entries_for_site(site)
    if entries is None:
        return None
    from .modules import get as get_module

    module = get_module("storefront_designs")
    applicable = module.applicable_keys() if module else set()
    for e in entries:
        if e.key == str(key):
            return e.in_library and e.key in applicable and e.item.fits_model(getattr(site, "business_model", ""))
    return False


def fallback_key(site) -> str:
    """A design to move a store to when its current one may no longer be used:
    a free design in its library that fits, else Template 1."""
    entries = entries_for_site(site) or []
    from .modules import get as get_module

    module = get_module("storefront_designs")
    applicable = module.applicable_keys() if module else {"1"}
    for e in entries:
        if e.item.is_system_default and e.key in applicable:
            return e.key
    return "1"


def fallback_site_design(site, *, reason: str) -> bool:
    """Move ``site`` off its current design if the owner no longer holds it."""
    from tenancy.models import Site

    entries = entries_for_site(site)
    if entries is None:
        return False
    current = site.theme_choice
    cur = next((e for e in entries if e.key == current), None)
    if cur is None or cur.item.is_system_default or cur.licence is not None:
        return False
    target = fallback_key(site)
    new_theme = Site.theme_for_choice(target, site.business_model)
    if not new_theme or new_theme == site.template_dir:
        return False
    old = site.template_dir
    site.theme = new_theme
    site.save(update_fields=["theme", "updated_at"])
    try:
        from commerce import activity

        activity.record(
            "store.design.changed", site=site, target=site, target_kind="Site",
            label=reason, actor_kind="system", changes={"theme": [old, new_theme]},
        )
    except Exception:
        pass
    return True


def enforce_site(site) -> str | None:
    """Settings page check: if this store's design was bought yearly and has
    lapsed (and nothing else covers it), move it back. Returns the design name."""
    entries = entries_for_site(site)
    if entries is None:
        return None
    cur = next((e for e in entries if e.is_current), None)
    if cur is None or cur.item.is_system_default or cur.licence is not None or not cur.lapsed:
        return None
    if fallback_site_design(site, reason=f"{cur.item.name} licence expired"):
        return cur.item.name
    return None


def mirror_legacy_purchase(purchase) -> None:
    """A legacy TemplatePurchase that settled late (pending reconcile) becomes a
    marketplace licence straight away."""
    from .models import AppItem, Licence
    from .registry import templates_app

    app = templates_app()
    if app is None or purchase is None or not getattr(purchase, "is_paid", False):
        return
    item = AppItem.objects.filter(app=app, key=purchase.template_key).first()
    if item is None:
        return
    from accounts.models import User

    user = User.objects.filter(email__iexact=purchase.email).first()
    if user is None:
        return
    licences.grant(
        owner_of(user), app, item=item, source=Licence.Source.PURCHASE, expires_at=purchase.expires_at,
        legacy_ref=f"OWT:{purchase.number}", price=purchase.base_amount, reason="Legacy template purchase",
    )
