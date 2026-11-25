"""Storefront website templates: the catalogue, licences and purchases.

The designs are code. Each one OneWooz builds and deploys is listed in
:data:`REGISTRY`; :func:`sync` mirrors that list into
:class:`~billing.models.WebsiteTemplate` rows automatically (no manual admin
step), so a newly deployed design shows up in the Django admin on its own. Staff
then decide, per design, its name, thumbnail, store type, whether it is free or
priced, the price, and whether a purchase is permanent or yearly.

Licences
    A free design can be used by anyone. A priced design must be bought first;
    the purchase (:class:`~billing.models.TemplatePurchase`) is bound to the
    store owner's **email**, so it covers every store that owner runs.

    * permanent: never expires;
    * yearly: expires one year after payment; buying again while still active
      extends from the current end date.

    A store already on a design before it became priced keeps it (nothing an
    owner already has is taken away by an admin price change). Only a store on a
    design whose **yearly purchase has lapsed** falls back to a free design, and
    the owner can buy it again to switch back.

Payment
    OneWooz's own Cashfree account (same credentials as subscriptions), plus GST
    at the subscription rate. Confirmed only by fetching the order's real status
    from Cashfree, from the return page, the webhook, or a later page load.

Adding a design later
    Add its entry to :data:`REGISTRY` and teach
    :meth:`tenancy.models.Site.theme_for_choice` which theme folder its key
    renders from. New entries default to hidden (``available: False``) so staff
    can price them before owners see them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

__all__ = [
    "REGISTRY",
    "YEARLY_DAYS",
    "sync",
    "catalog",
    "options_for_site",
    "can_use",
    "active_purchase",
    "start_purchase",
    "confirm_purchase",
    "apply_to_site",
    "enforce_expiry",
    "fallback_key",
]

#: Every storefront design deployed in code. ``key`` is the owner-facing choice
#: (see Site.theme_for_choice). Rows for these are created automatically.
REGISTRY: tuple[dict, ...] = (
    {
        "key": "1",
        "name": "Template 1",
        "description": (
            "The original design for your business: a catalogue storefront for trade buyers (B2B), "
            "or a modern storefront with bold banners, shoppable reels and a quick cart drawer (D2C)."
        ),
        "order": 1,
        "available": True,
    },
    {
        "key": "2",
        "name": "Template 2",
        "description": (
            "Pebble style: soft colours, rounded cards, a floating header and a slide-out cart. "
            "Works for D2C and B2B."
        ),
        "order": 2,
        "available": True,
    },
    {
        "key": "3",
        "name": "Template 3",
        "description": (
            "Eurus style: calm botanical colours, elegant serif accents, pill buttons and a "
            "full-width hero. Works for D2C and B2B."
        ),
        "order": 3,
        "available": True,
    },
)

#: Length of a yearly licence.
YEARLY_DAYS = 365


def _models():
    from .models import TemplatePurchase, WebsiteTemplate

    return WebsiteTemplate, TemplatePurchase


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
def sync() -> None:
    """Create a row for every registered design that has none yet.

    Never edits an existing row (those belong to staff once created).
    """
    WebsiteTemplate, _ = _models()
    try:
        existing = set(WebsiteTemplate.objects.values_list("key", flat=True))
    except Exception:  # Table not migrated yet: the pickers fall back to code.
        return
    for spec in REGISTRY:
        if spec["key"] in existing:
            continue
        try:
            WebsiteTemplate.objects.get_or_create(
                key=spec["key"],
                defaults={
                    "name": spec["name"],
                    "description": spec.get("description", "")[:300],
                    "order": spec.get("order", 0),
                    "is_available": bool(spec.get("available", False)),
                },
            )
        except Exception:
            continue


def catalog() -> list:
    """Every design row (synced), in display order."""
    WebsiteTemplate, _ = _models()
    sync()
    try:
        return list(WebsiteTemplate.objects.order_by("order", "key"))
    except Exception:
        return []


def registered_keys() -> set[str]:
    """The design keys deployed in code."""
    return {spec["key"] for spec in REGISTRY}


_registered_keys = registered_keys


def _row(key: str):
    for t in catalog():
        if t.key == str(key):
            return t
    return None


def fallback_key(business_model: str = "") -> str:
    """The first free, available design that fits this business model."""
    for t in catalog():
        if t.key in _registered_keys() and t.is_available and not t.is_priced and t.fits(business_model):
            return t.key
    return "1"


# --------------------------------------------------------------------------
# Licences
# --------------------------------------------------------------------------
def _owner_email(site) -> str:
    owner = getattr(site, "owner", None)
    return (getattr(owner, "email", "") or "").strip()


def active_purchase(email: str, key: str):
    """The paid, unexpired purchase of ``key`` for ``email`` (latest end first)."""
    _, TemplatePurchase = _models()
    if not email:
        return None
    from django.db.models import Q

    rows = list(
        TemplatePurchase.objects.filter(
            email__iexact=email,
            template_key=str(key),
            status=TemplatePurchase.Status.PAID,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))[:20]
    )
    if not rows:
        return None
    # A permanent licence wins; otherwise the one running longest.
    rows.sort(key=lambda p: (p.expires_at is not None, -(p.expires_at.timestamp() if p.expires_at else 0)))
    return rows[0]


def _lapsed_purchase(email: str, key: str):
    _, TemplatePurchase = _models()
    if not email:
        return None
    return (
        TemplatePurchase.objects.filter(
            email__iexact=email,
            template_key=str(key),
            status=TemplatePurchase.Status.PAID,
            expires_at__isnull=False,
            expires_at__lte=timezone.now(),
        )
        .order_by("-expires_at")
        .first()
    )


def _legacy_can_use(site, key: str) -> bool:
    """May ``site`` switch to (or stay on) design ``key``?"""
    key = str(key)
    if key not in _registered_keys():
        return False
    row = _row(key)
    if row is None:
        return True  # Catalogue not migrated yet: behave as before (all free).
    if getattr(site, "theme_choice", None) == key:
        return True  # Already on it (grandfathered or licensed).
    if not row.is_available or not row.fits(getattr(site, "business_model", "")):
        return False
    if not row.is_priced:
        return True
    return active_purchase(_owner_email(site), key) is not None


@dataclass
class TemplateOption:
    """One design as the store's design picker shows it."""

    template: object
    key: str
    name: str
    description: str
    thumbnail_url: str
    is_current: bool
    is_priced: bool
    price: Decimal
    gst_amount: Decimal
    total: Decimal
    is_yearly: bool
    license_label: str
    owned: bool
    expires_at: object
    lapsed: bool
    can_use: bool
    #: Set when the option comes from the marketplace library: buying and
    #: renewing happen on OneWooz Apps, not from Settings.
    marketplace_url: str = ""

    @property
    def can_buy(self) -> bool:
        """Priced, and either not owned yet or a yearly licence open to renewal."""
        return self.is_priced and (not self.owned or self.is_yearly)


def _gst(amount: Decimal) -> "tuple[Decimal, Decimal, Decimal]":
    from . import cycles
    from .pricing import gst_rate

    rate = gst_rate()
    base = cycles.money(Decimal(amount or 0))
    gst = cycles.money(base * rate / Decimal(100))
    return rate, gst, cycles.money(base + gst)


def _legacy_options_for_site(site) -> list[TemplateOption]:
    """The designs this store's owner may see, with price and licence state."""
    email = _owner_email(site)
    current = getattr(site, "theme_choice", "")
    out: list[TemplateOption] = []
    for t in catalog():
        if t.key not in _registered_keys():
            continue
        is_current = t.key == current
        if not is_current and (not t.is_available or not t.fits(getattr(site, "business_model", ""))):
            continue
        purchase = active_purchase(email, t.key) if t.is_priced else None
        lapsed = bool(t.is_priced and purchase is None and _lapsed_purchase(email, t.key))
        _, gst, total = _gst(t.price) if t.is_priced else (Decimal("0"), Decimal("0"), Decimal("0"))
        try:
            thumb = t.thumbnail.url if t.thumbnail else ""
        except Exception:
            thumb = ""
        out.append(
            TemplateOption(
                template=t,
                key=t.key,
                name=t.name,
                description=t.description,
                thumbnail_url=thumb,
                is_current=is_current,
                is_priced=t.is_priced,
                price=t.price or Decimal("0"),
                gst_amount=gst,
                total=total,
                is_yearly=t.is_yearly,
                license_label=("per year" if t.is_yearly else "one-time, yours for good"),
                owned=purchase is not None,
                expires_at=getattr(purchase, "expires_at", None),
                lapsed=lapsed,
                can_use=is_current or (not t.is_priced) or purchase is not None,
            )
        )
    return out


def _legacy_creation_options(owner) -> list:
    """Designs offered on the create-store form (available ones), each with
    ``locked`` set when it is priced and this owner holds no licence."""
    email = (getattr(owner, "email", "") or "").strip()
    rows = []
    for t in catalog():
        if t.key not in _registered_keys() or not t.is_available:
            continue
        locked = t.is_priced and active_purchase(email, t.key) is None
        rows.append({"template": t, "key": t.key, "name": t.name, "locked": locked,
                     "type": t.template_type, "price": t.price, "is_yearly": t.is_yearly})
    return rows


def _legacy_creation_choice(owner, choice: str, business_model: str) -> str:
    """The design key a new store gets: the owner's pick when they may use it,
    else the first free design for the business model."""
    choice = str(choice or "").strip()
    email = (getattr(owner, "email", "") or "").strip()
    row = _row(choice)
    if row is None:
        return choice if choice in _registered_keys() else "1"
    usable = (
        row.is_available
        and row.fits(business_model)
        and (not row.is_priced or active_purchase(email, choice) is not None)
    )
    return choice if usable else fallback_key(business_model)


# --------------------------------------------------------------------------
# Purchases
# --------------------------------------------------------------------------
class PurchaseError(Exception):
    """Shown to the owner as is."""


def start_purchase(request, site, key: str, *, return_url_for, notify_url: str) -> dict:
    """Create a pending purchase + Cashfree order for ``key``; launch params back.

    ``return_url_for(purchase)`` builds the absolute return URL (it needs the
    purchase id). Raises :class:`PurchaseError` with an owner-facing message.
    """
    from . import cashfree

    WebsiteTemplate, TemplatePurchase = _models()
    row = _row(key)
    if row is None or not row.is_available or row.key not in _registered_keys():
        raise PurchaseError("That design is not available.")
    if not row.fits(site.business_model):
        raise PurchaseError("That design is not available for this type of store.")
    if not row.is_priced:
        raise PurchaseError("That design is free. Just select it and click Use this design.")
    email = _owner_email(site)
    if not email:
        raise PurchaseError("Your account has no email address to attach the purchase to.")
    if not row.is_yearly and active_purchase(email, key) is not None:
        raise PurchaseError(f"You already own {row.name}.")
    if not cashfree.is_configured():
        raise PurchaseError("Online payment isn't available right now. Please contact us.")

    rate, gst, total = _gst(row.price)
    owner = getattr(site, "owner", None)
    purchase = TemplatePurchase.objects.create(
        template=row,
        template_key=row.key,
        template_name=row.name,
        license_type=row.license_type,
        email=email,
        user=request.user if getattr(request.user, "is_authenticated", False) else None,
        site_ref=site.id,
        currency=row.currency or "INR",
        base_amount=row.price,
        gst_rate=rate,
        gst_amount=gst,
        total_amount=total,
        status=TemplatePurchase.Status.CREATED,
    )
    cf_order_id = f"OWT-{purchase.number}-{purchase.id.hex[:8]}"
    try:
        params = cashfree.create_payment_order(
            cf_order_id=cf_order_id,
            amount=total,
            currency=purchase.currency,
            customer_id=purchase.number,
            customer_name=(getattr(owner, "display_name", "") or getattr(owner, "get_full_name", lambda: "")() or site.name or "")[:100],
            customer_email=email,
            customer_phone=(getattr(site, "support_phone", "") or "")[:15],
            return_url=return_url_for(purchase),
            notify_url=notify_url,
            note=f"OneWooz website template {row.name}",
            tags={"kind": "website_template", "template": row.key},
        )
    except cashfree.CashfreeError as exc:
        purchase.status = TemplatePurchase.Status.FAILED
        purchase.save(update_fields=["status", "updated_at"])
        raise PurchaseError("We couldn't start the payment. Please try again.") from exc

    purchase.cf_order_id = cf_order_id
    purchase.status = TemplatePurchase.Status.PENDING
    purchase.gateway_status = str((params.get("data") or {}).get("order_status") or "")
    purchase.save(update_fields=["cf_order_id", "status", "gateway_status", "updated_at"])
    return {"payment_session_id": params["payment_session_id"], "mode": params["mode"], "purchase": purchase}


def confirm_purchase(purchase) -> bool:
    """Ask Cashfree for the real status; mark paid (idempotently) when PAID."""
    from . import cashfree

    _, TemplatePurchase = _models()
    if purchase is None:
        return False
    if purchase.is_paid:
        return True
    status, data = cashfree.fetch_order_status(purchase.cf_order_id)
    if status and status != purchase.gateway_status:
        purchase.gateway_status = status
        purchase.save(update_fields=["gateway_status", "updated_at"])
    if status != "PAID":
        if status in ("EXPIRED", "TERMINATED", "CANCELLED"):
            purchase.status = TemplatePurchase.Status.FAILED
            purchase.save(update_fields=["status", "updated_at"])
        return False

    with transaction.atomic():
        locked = TemplatePurchase.objects.select_for_update().get(pk=purchase.pk)
        if locked.is_paid:
            purchase.refresh_from_db()
            return True
        now = timezone.now()
        expires = None
        if locked.license_type == "yearly":
            start = now
            current = active_purchase(locked.email, locked.template_key)
            if current is not None and current.expires_at and current.expires_at > now:
                start = current.expires_at  # renewal while active extends it
            expires = start + timedelta(days=YEARLY_DAYS)
        locked.status = TemplatePurchase.Status.PAID
        locked.paid_at = now
        locked.expires_at = expires
        locked.cf_payment_id = str(data.get("cf_order_id") or "")[:64]
        locked.save(update_fields=["status", "paid_at", "expires_at", "cf_payment_id", "updated_at"])
    purchase.refresh_from_db()

    try:
        from core import audit

        audit.record(
            "billing.template.purchased",
            target=purchase,
            target_kind="TemplatePurchase",
            target_label=purchase.number,
            actor_kind="owner",
            context={
                "email": purchase.email,
                "template": purchase.template_key,
                "total": f"{purchase.total_amount:.2f}",
                "expires": purchase.expires_at.isoformat() if purchase.expires_at else "permanent",
            },
        )
    except Exception:
        pass
    apply_to_site(purchase)
    try:
        from marketplace import library

        library.mirror_legacy_purchase(purchase)
    except Exception:
        pass
    return True


def purchase_for_cf_id(cf_order_id: str):
    _, TemplatePurchase = _models()
    if not cf_order_id or not str(cf_order_id).startswith("OWT-"):
        return None
    return TemplatePurchase.objects.filter(cf_order_id=cf_order_id).first()


def apply_to_site(purchase) -> bool:
    """Switch the store the design was bought from onto it (once paid)."""
    if purchase is None or not purchase.is_current or not purchase.site_ref:
        return False
    from tenancy.models import Site

    site = Site.objects.filter(pk=purchase.site_ref, is_deleted=False).select_related("owner").first()
    if site is None or _owner_email(site).lower() != (purchase.email or "").lower():
        return False
    new_theme = Site.theme_for_choice(purchase.template_key, site.business_model)
    if site.template_dir == new_theme:
        return True
    old = site.template_dir
    site.theme = new_theme
    site.save(update_fields=["theme", "updated_at"])
    try:
        from commerce import activity

        activity.record(
            "store.design.changed", site=site, target=site, target_kind="Site",
            label=purchase.template_name, actor_kind="owner",
            changes={"theme": [old, new_theme]},
        )
    except Exception:
        pass
    return True


def reconcile_pending(site) -> int:
    """Confirm this owner's recent pending purchases (closed the payment tab
    before returning, webhook missed). Returns how many became paid."""
    _, TemplatePurchase = _models()
    email = _owner_email(site)
    if not email:
        return 0
    since = timezone.now() - timedelta(days=2)
    pending = list(
        TemplatePurchase.objects.filter(
            email__iexact=email, status=TemplatePurchase.Status.PENDING, created_at__gte=since
        ).exclude(cf_order_id="")[:3]
    )
    return sum(1 for p in pending if confirm_purchase(p))


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------
def _legacy_enforce_expiry(site) -> "str | None":
    """Move ``site`` off a design whose yearly licence has lapsed.

    Returns the lapsed design's name when the store was switched, else None.
    A store on a priced design with no purchase at all (it was on the design
    before it was priced) is left alone.
    """
    key = getattr(site, "theme_choice", "")
    row = _row(key)
    if row is None or not row.is_priced:
        return None
    email = _owner_email(site)
    if active_purchase(email, key) is not None:
        return None
    lapsed = _lapsed_purchase(email, key)
    if lapsed is None:
        return None
    from tenancy.models import Site

    target_key = fallback_key(site.business_model)
    new_theme = Site.theme_for_choice(target_key, site.business_model)
    if new_theme == site.template_dir:
        return None
    old = site.template_dir
    site.theme = new_theme
    site.save(update_fields=["theme", "updated_at"])
    try:
        from commerce import activity

        activity.record(
            "store.design.changed", site=site, target=site, target_kind="Site",
            label=f"{row.name} licence expired", actor_kind="system",
            changes={"theme": [old, new_theme]},
        )
    except Exception:
        pass
    return row.name


def enforce_all_expired() -> int:
    """Daily sweep: every store on a design whose yearly licence lapsed."""
    _, TemplatePurchase = _models()
    from tenancy.models import Site

    now = timezone.now()
    window = now - timedelta(days=YEARLY_DAYS * 2)
    lapsed = (
        TemplatePurchase.objects.filter(
            status=TemplatePurchase.Status.PAID,
            expires_at__isnull=False,
            expires_at__lte=now,
            expires_at__gte=window,
        )
        .order_by()
        .values_list("email", flat=True)
        .distinct()
    )
    switched = 0
    for email in set(e.lower() for e in lapsed if e):
        for site in Site.objects.filter(owner__email__iexact=email, is_deleted=False).select_related("owner").defer("content"):
            try:
                if enforce_expiry(site):
                    switched += 1
            except Exception:
                continue
    return switched


# --------------------------------------------------------------------------
# Marketplace-first (OneWooz Templates)
# --------------------------------------------------------------------------
# Designs are now sold and licensed through the marketplace app "OneWooz
# Templates" (marketplace.library). Settings and the create-store form show the
# owner's *library* only: Template 1, designs they added or bought, and the
# design the store is on now. Each wrapper falls back to the legacy behaviour if
# the marketplace is unavailable (not migrated yet, or any error), so the
# dashboard never breaks because of it.

def _lib():
    try:
        from marketplace import library

        return library
    except Exception:
        return None


def options_for_site(site) -> list[TemplateOption]:
    lib = _lib()
    if lib is not None:
        try:
            entries = lib.entries_for_site(site)
        except Exception:
            entries = None
        if entries is not None:
            return _options_from_library(site, entries)
    return _legacy_options_for_site(site)


def _options_from_library(site, entries) -> list[TemplateOption]:
    keys = _registered_keys()
    bm = getattr(site, "business_model", "")
    out: list[TemplateOption] = []
    for e in entries:
        item = e.item
        if not e.in_library or item.key not in keys:
            continue
        if not e.is_current and not item.fits_model(bm):
            continue
        lic = e.licence
        try:
            thumb = item.thumbnail.url if item.thumbnail else ""
        except Exception:
            thumb = ""
        _, gst, total = _gst(item.price) if item.is_priced else (Decimal("0"), Decimal("0"), Decimal("0"))
        out.append(
            TemplateOption(
                template=item,
                key=item.key,
                name=item.name,
                description=item.description,
                thumbnail_url=thumb,
                is_current=e.is_current,
                is_priced=item.is_priced,
                price=item.price or Decimal("0"),
                gst_amount=gst,
                total=total,
                is_yearly=item.is_yearly,
                license_label=("per year" if item.is_yearly else "one-time, yours for good"),
                owned=bool(lic is not None and lic.source in ("purchase", "admin_grant")),
                expires_at=getattr(lic, "expires_at", None),
                lapsed=e.lapsed,
                can_use=True,
                # Resolved to a real link per request by the settings view.
                marketplace_url="marketplace",
            )
        )
    return out


def can_use(site, key: str) -> bool:
    lib = _lib()
    if lib is not None:
        try:
            verdict = lib.can_use_design(site, key)
        except Exception:
            verdict = None
        if verdict is not None:
            return bool(verdict) or getattr(site, "theme_choice", None) == str(key)
    return _legacy_can_use(site, key)


def creation_options(owner) -> list:
    lib = _lib()
    if lib is not None:
        try:
            keys = lib.library_keys_for_owner(owner)
        except Exception:
            keys = None
        if keys is not None:
            try:
                from marketplace.models import AppItem
                from marketplace.registry import templates_app

                app = templates_app()
                rows = []
                for it in AppItem.objects.filter(app=app).order_by("order", "name"):
                    if it.key not in keys or it.key not in _registered_keys():
                        continue
                    rows.append({"template": it, "key": it.key, "name": it.name, "locked": False,
                                 "type": it.fits, "price": it.price, "is_yearly": it.is_yearly})
                if rows:
                    return rows
            except Exception:
                pass
    return _legacy_creation_options(owner)


def creation_choice(owner, choice: str, business_model: str) -> str:
    lib = _lib()
    if lib is not None:
        try:
            keys = lib.library_keys_for_owner(owner)
        except Exception:
            keys = None
        if keys is not None:
            choice = str(choice or "").strip()
            if choice in keys and choice in _registered_keys():
                return choice
            return "1"
    return _legacy_creation_choice(owner, choice, business_model)


def enforce_expiry(site) -> "str | None":
    lib = _lib()
    if lib is not None:
        try:
            if lib.entries_for_site(site) is not None:
                return lib.enforce_site(site)
        except Exception:
            return None
    return _legacy_enforce_expiry(site)
