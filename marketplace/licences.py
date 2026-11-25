"""Licences and installations: the one answer to "what does this owner have".

Every grant, extension, expiry and revoke goes through here, writes a
:class:`~marketplace.models.LicenceEvent` and a ``core.audit`` row, and then
asks for a storefront recompile (async) so the owner's stores reflect it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger("onewooz.marketplace")

__all__ = [
    "Owner",
    "owner_of",
    "active_licences",
    "has_app",
    "app_licence",
    "installed_apps",
    "is_installed",
    "grant",
    "install",
    "uninstall",
    "revoke",
    "expire_due",
    "has_paid_plan",
]


@dataclass(frozen=True)
class Owner:
    ref: UUID
    email: str
    name: str = ""


def owner_of(user_or_site) -> Owner | None:
    """An :class:`Owner` from a user, a site, or an Owner."""
    if user_or_site is None:
        return None
    if isinstance(user_or_site, Owner):
        return user_or_site
    user = getattr(user_or_site, "owner", None) if hasattr(user_or_site, "owner_id") else user_or_site
    if user is None or not getattr(user, "pk", None):
        return None
    name = getattr(user, "display_name", "") or ""
    return Owner(ref=user.pk, email=(getattr(user, "email", "") or "").strip().lower(), name=name)


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def _current_q():
    now = timezone.now()
    return Q(status="active") & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def active_licences(owner: Owner, *, app=None) -> list:
    from .models import Licence

    if owner is None:
        return []
    qs = Licence.objects.filter(owner_ref=owner.ref).filter(_current_q()).select_related("app", "item")
    if app is not None:
        qs = qs.filter(app=app)
    return list(qs[:500])


def has_paid_plan(email: str) -> bool:
    """An active paid / one-time OneWooz subscription (not a free plan)."""
    if not email:
        return False
    try:
        from billing.models import SubscriptionOrder, SubscriptionPlan

        active = (
            SubscriptionOrder.objects.filter(
                customer_email__iexact=email,
                status=SubscriptionOrder.Status.PAID,
                current_period_end__gte=timezone.localdate(),
            )
            .select_related("plan")
            .order_by("-current_period_end")
            .first()
        )
        if active is None:
            return False
        return active.plan is None or active.plan.plan_type != SubscriptionPlan.Kind.FREE
    except Exception:
        return False


def app_licence(owner: Owner, app):
    """The owner's current app-level licence for ``app`` (longest running), or None."""
    rows = [lic for lic in active_licences(owner, app=app) if lic.item_id is None]
    if not rows:
        return None
    rows.sort(key=lambda l: (l.expires_at is not None, -(l.expires_at.timestamp() if l.expires_at else 0)))
    return rows[0]


def has_app(owner: Owner, app) -> bool:
    """May this owner use ``app`` at all (independent of installation)?"""
    if owner is None or app is None:
        return False
    if app.is_default:
        return True
    if app.pricing == app.Pricing.PLAN_INCLUDED:
        return has_paid_plan(owner.email) or app_licence(owner, app) is not None
    if app.pricing == app.Pricing.FREE:
        return True
    return app_licence(owner, app) is not None


def is_installed(owner: Owner, app) -> bool:
    from .models import Installation

    if owner is None or app is None:
        return False
    if app.is_default:
        # Pre-installed for everyone; a system app can never be uninstalled.
        if app.is_system:
            return True
        row = Installation.objects.filter(owner_ref=owner.ref, app=app).first()
        return row is None or row.status == Installation.Status.ACTIVE
    return Installation.objects.filter(
        owner_ref=owner.ref, app=app, status=Installation.Status.ACTIVE
    ).exists()


def installed_apps(owner: Owner) -> list:
    """Apps on this owner's dashboard: installed (or pre-installed) and usable.
    Draft apps are excluded; retired apps stay for owners who have them."""
    from .models import App, Installation

    if owner is None:
        return []
    rows = {
        i.app_id: i
        for i in Installation.objects.filter(owner_ref=owner.ref).select_related("app")
    }
    out = []
    candidates = list(App.objects.exclude(status=App.Status.DRAFT).filter(
        Q(is_default=True) | Q(pk__in=list(rows.keys()))
    ).order_by("order", "name"))
    for app in candidates:
        inst = rows.get(app.pk)
        if app.is_default:
            if not app.is_system and inst is not None and inst.status != Installation.Status.ACTIVE:
                continue
        elif inst is None or inst.status != Installation.Status.ACTIVE:
            continue
        if not has_app(owner, app):
            continue
        out.append(app)
    return out


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def _event(licence, event: str, *, actor_kind="system", actor_label="", reason="", context=None, request=None):
    from core import audit

    from .models import LicenceEvent

    try:
        LicenceEvent.objects.create(
            licence=licence, event=event, actor_kind=actor_kind, actor_label=actor_label[:255],
            reason=(reason or "")[:300], context=context or {},
        )
    except Exception:
        logger.exception("licence event write failed")
    audit.record(
        f"marketplace.licence.{event}",
        request=request,
        actor_kind=actor_kind,
        target=licence,
        target_kind="Licence",
        target_label=licence.name_snapshot or str(licence.pk),
        context={
            "owner": licence.owner_email,
            "app": licence.app.slug,
            "item": licence.item_key,
            "source": licence.source,
            "expires": licence.expires_at.isoformat() if licence.expires_at else "permanent",
            "reason": reason,
        },
    )


def grant(
    owner: Owner,
    app,
    *,
    item=None,
    source: str,
    expires_at=None,
    period_days: int | None = None,
    site_ref=None,
    order_line=None,
    legacy_ref: str = "",
    price=None,
    actor_kind: str = "system",
    actor_label: str = "",
    reason: str = "",
    request=None,
):
    """Grant (or extend) a licence. Idempotent for free adds and legacy imports.

    ``period_days`` extends from the current end when an active licence of the
    same app/item already runs (renewal while active never loses days).
    """
    from .models import Licence

    now = timezone.now()
    with transaction.atomic():
        if legacy_ref:
            existing = Licence.objects.filter(legacy_ref=legacy_ref).first()
            if existing is not None:
                return existing
        if order_line is not None:
            existing = Licence.objects.filter(order_line=order_line).first()
            if existing is not None:
                return existing

        same = Licence.objects.select_for_update().filter(
            owner_ref=owner.ref, app=app, item=item
        ).filter(_current_q())
        if site_ref is None:
            same = same.filter(site_ref__isnull=True)
        current = list(same)

        if source in ("free_add", "default", "grandfathered") and any(
            l.expires_at is None and (site_ref is None or l.site_ref == site_ref) for l in current
        ):
            return next(l for l in current if l.expires_at is None)

        starts = now
        if period_days:
            running = [l for l in current if l.expires_at and l.expires_at > now]
            if running:
                starts = max(l.expires_at for l in running)
            expires_at = starts + timedelta(days=period_days)

        name = item.name if item is not None else app.name
        licence = Licence.objects.create(
            owner_ref=owner.ref,
            owner_email=owner.email,
            app=app,
            item=item,
            item_key=getattr(item, "key", "") or "",
            source=source,
            site_ref=site_ref,
            starts_at=now,
            expires_at=expires_at,
            order_line=order_line,
            legacy_ref=legacy_ref,
            name_snapshot=name[:120],
            price_snapshot=price if price is not None else (getattr(item, "price", None) or app.price or 0),
            licence_type_snapshot=(
                f"{period_days} days" if period_days else ("expires" if expires_at else "permanent")
            ),
        )
    _event(licence, "granted", actor_kind=actor_kind, actor_label=actor_label, reason=reason,
           context={"source": source}, request=request)
    from . import modules

    modules.call_hook(app, "on_licence_change", licence=licence, event="granted")
    _invalidate(owner)
    return licence


def install(owner: Owner, app, *, source: str = "owner", reauth_method: str = "", request=None):
    """Put ``app`` on the owner's dashboard (idempotent)."""
    from core import audit

    from .models import Installation

    now = timezone.now()
    row, created = Installation.objects.get_or_create(
        owner_ref=owner.ref,
        app=app,
        defaults={
            "owner_email": owner.email,
            "source": source,
            "installed_at": now,
            "reauth_method": reauth_method,
            "reauth_at": now if reauth_method else None,
        },
    )
    if not created and row.status != Installation.Status.ACTIVE:
        if row.status == Installation.Status.DISABLED:
            return row  # staff disabled it; the owner cannot override that
        row.status = Installation.Status.ACTIVE
        row.installed_at = now
        row.uninstalled_at = None
        row.reauth_method = reauth_method
        row.reauth_at = now if reauth_method else None
        row.save()
        created = True
    if created:
        audit.record(
            "marketplace.app.installed", request=request, target=row, target_kind="Installation",
            target_label=app.name, context={"owner": owner.email, "app": app.slug, "source": source,
                                            "reauth": reauth_method},
        )
        from . import modules, tasks

        modules.call_hook(app, "on_install", owner_ref=owner.ref, owner_email=owner.email)
        tasks.enqueue_compile_owner(owner.ref)
    _invalidate(owner)
    return row


def uninstall(owner: Owner, app, *, reauth_method: str = "", request=None) -> bool:
    from core import audit

    from .models import Installation

    if app.is_system:
        return False
    row = Installation.objects.filter(owner_ref=owner.ref, app=app).first()
    if row is None:
        if not app.is_default:
            return False
        row = Installation.objects.create(owner_ref=owner.ref, owner_email=owner.email, app=app,
                                          source=Installation.Source.DEFAULT)
    if row.status == Installation.Status.UNINSTALLED:
        return True
    row.status = Installation.Status.UNINSTALLED
    row.uninstalled_at = timezone.now()
    row.reauth_method = reauth_method
    row.save()
    audit.record(
        "marketplace.app.uninstalled", request=request, target=row, target_kind="Installation",
        target_label=app.name, context={"owner": owner.email, "app": app.slug, "reauth": reauth_method},
    )
    from . import modules, tasks

    modules.call_hook(app, "on_uninstall", owner_ref=owner.ref, owner_email=owner.email)
    tasks.enqueue_compile_owner(owner.ref)
    _invalidate(owner)
    return True


def revoke(licence, *, reason: str, actor_label: str = "", actor_kind: str = "admin", request=None) -> None:
    if licence.status == licence.Status.REVOKED:
        return
    licence.status = licence.Status.REVOKED
    licence.revoked_reason = (reason or "")[:300]
    licence.revoked_by = (actor_label or "")[:255]
    licence.save(update_fields=["status", "revoked_reason", "revoked_by", "updated_at"])
    _event(licence, "revoked", actor_kind=actor_kind, actor_label=actor_label, reason=reason, request=request)
    from . import modules, tasks

    modules.call_hook(licence.app, "on_licence_change", licence=licence, event="revoked")
    tasks.enqueue_compile_owner(licence.owner_ref)
    _invalidate(Owner(licence.owner_ref, licence.owner_email))


def restore(licence, *, reason: str, actor_label: str = "", request=None) -> None:
    if licence.status == licence.Status.ACTIVE:
        return
    licence.status = licence.Status.ACTIVE
    licence.revoked_reason = ""
    licence.save(update_fields=["status", "revoked_reason", "updated_at"])
    _event(licence, "restored", actor_kind="admin", actor_label=actor_label, reason=reason, request=request)
    from . import tasks

    tasks.enqueue_compile_owner(licence.owner_ref)
    _invalidate(Owner(licence.owner_ref, licence.owner_email))


def expire_due(limit: int = 500) -> int:
    """Mark lapsed licences expired and run their hooks. Returns the count."""
    from .models import Licence

    now = timezone.now()
    due = list(
        Licence.objects.filter(status=Licence.Status.ACTIVE, expires_at__isnull=False, expires_at__lte=now)
        .select_related("app")[:limit]
    )
    done = 0
    for licence in due:
        updated = Licence.objects.filter(pk=licence.pk, status=Licence.Status.ACTIVE).update(
            status=Licence.Status.EXPIRED, updated_at=now
        )
        if not updated:
            continue
        licence.status = Licence.Status.EXPIRED
        done += 1
        _event(licence, "expired")
        # A renewal bought before expiry is another active licence; only act
        # when nothing else still covers the same app/item.
        still = Licence.objects.filter(
            owner_ref=licence.owner_ref, app=licence.app, item=licence.item
        ).filter(_current_q()).exists()
        if not still:
            from . import modules, tasks

            modules.call_hook(licence.app, "on_licence_change", licence=licence, event="expired")
            tasks.enqueue_compile_owner(licence.owner_ref)
        _invalidate(Owner(licence.owner_ref, licence.owner_email))
    return done


def _invalidate(owner: Owner) -> None:
    try:
        from . import api

        api.invalidate_owner(owner.ref)
    except Exception:
        pass
