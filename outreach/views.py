"""Public, login-free tracking endpoints reached from a sent email.

Each carries a purpose-salted signed token (:mod:`core.tokens`) bound to one
:class:`~outreach.models.EmailDelivery`. They never raise on a bad/expired
token  an email client hitting a stale pixel must still get a clean 1×1, and a
dead link must land somewhere sane rather than a 500.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from core import tokens
from outreach.models import EmailDelivery, Suppression
from outreach.services import rendering

# A 1×1 transparent PNG.
_PIXEL = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\x99c``\x00\x00\x00\x04"
    b"\x00\x01\x0b\xe7\x9d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _delivery_from(purpose: str, token: str):
    try:
        payload = tokens.unsign(purpose, token)
    except tokens.TokenError:
        return None
    did = payload.get("d")
    if not did:
        return None
    return (
        EmailDelivery.all_objects.filter(pk=did)
        .select_related("campaign", "site")
        .first()
    )


@never_cache
def open_pixel(request, token: str):
    delivery = _delivery_from(rendering.OPEN_PURPOSE, token)
    if delivery is not None:
        now = timezone.now()
        updates = {"open_count": (delivery.open_count or 0) + 1, "last_opened_at": now}
        if not delivery.first_opened_at:
            updates["first_opened_at"] = now
        EmailDelivery.all_objects.filter(pk=delivery.pk).update(**updates)
    return HttpResponse(_PIXEL, content_type="image/png")


@never_cache
def click(request, token: str):
    delivery = _delivery_from(rendering.CLICK_PURPOSE, token)
    if delivery is None:
        return redirect("/")

    if not delivery.clicked_at:
        EmailDelivery.all_objects.filter(pk=delivery.pk, clicked_at__isnull=True).update(
            clicked_at=timezone.now()
        )

    # Built-in designs sign each button's own destination into the token.
    target = ""
    try:
        signed = tokens.unsign(rendering.CLICK_PURPOSE, token).get("u") or ""
    except tokens.TokenError:
        signed = ""
    if isinstance(signed, str) and signed.lower().startswith(("https://", "http://")):
        target = signed
    target = target or (getattr(delivery.campaign, "cta_url", "") or "").strip()
    if not target:
        from core.links import storefront_origin

        slug = getattr(delivery.site, "slug", "") or ""
        target = storefront_origin(slug) if slug else "/"
    return redirect(target)


@csrf_exempt
@never_cache
def unsubscribe(request, token: str):
    """Unsubscribe one recipient from one store's marketing email.

    GET only shows a confirmation button: mail security scanners (Outlook Safe
    Links, corporate gateways) open every link in an email, and unsubscribing
    on GET would silently remove real customers. The button POSTs back here.

    POST performs it, and is also the RFC 8058 one-click target that Gmail and
    Yahoo call from the ``List-Unsubscribe-Post`` header (no CSRF token is
    possible there; the signed token is the authorisation).
    """
    delivery = _delivery_from(rendering.UNSUB_PURPOSE, token)
    if delivery is None:
        return render(request, "outreach/unsubscribed.html", {"invalid": True}, status=400)

    brand = getattr(delivery.site, "name", "") or "this store"
    email = (delivery.recipient_email or "").strip().lower()

    if request.method != "POST":
        already = Suppression.all_objects.filter(site=delivery.site, email=email).exists()
        return render(
            request,
            "outreach/unsubscribed.html",
            {"brand": brand, "confirm": not already, "email": email},
        )

    now = timezone.now()
    if not delivery.unsubscribed_at:
        EmailDelivery.all_objects.filter(pk=delivery.pk, unsubscribed_at__isnull=True).update(
            unsubscribed_at=now
        )
    # Record a store-scoped do-not-contact entry (idempotent on the unique key).
    Suppression.all_objects.get_or_create(
        site=delivery.site,
        email=email,
        defaults={"reason": Suppression.Reason.UNSUBSCRIBED},
    )
    return render(request, "outreach/unsubscribed.html", {"brand": brand})
