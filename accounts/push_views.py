"""Browser push notification endpoints (see :mod:`accounts.webpush`).

Mounted with the ``accounts`` routes on the app surface, which is never behind
the renewal wall or the plan / staff gates, so the service worker and the
subscription calls keep working for every signed-in owner.
"""

from __future__ import annotations

import json
import uuid

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import ratelimit

from . import webpush
from .models import PushSubscription


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def _impersonating(request) -> bool:
    return bool(getattr(request, "impersonation", None))


@require_GET
def service_worker(request):
    """The push service worker. Served from the app origin (never the CDN: a
    service worker must be same-origin) at the surface root, so its scope
    covers the whole dashboard. Always revalidated, so an update reaches
    browsers on their next visit."""
    body = render_to_string(
        "accounts/push/sw.js",
        {
            "icon": static("img/OneWooz_favicon_white_theme.png"),
            "badge": static("img/OneWooz_favicon_white_theme.png"),
        },
    )
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache, max-age=0"
    return response


@require_GET
def manifest(request):
    """Web app manifest for the dashboard. Lets an owner add OneWooz to their
    home screen, which is what iPhone and iPad require before Safari allows
    push notifications at all."""
    start = reverse("ud:dashboard")
    scope = reverse("accounts:service_worker").rsplit("/", 1)[0] + "/"
    icon = static("img/OneWooz_favicon_white_theme.png")
    data = {
        "name": "OneWooz",
        "short_name": "OneWooz",
        "description": "Run your business, not just your website.",
        "id": start,
        "start_url": start,
        "scope": scope,
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#4b2bf5",
        "icons": [
            {"src": icon, "sizes": "any", "type": "image/png", "purpose": "any"},
        ],
    }
    response = HttpResponse(json.dumps(data), content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=3600"
    return response


@login_required
@require_POST
def subscribe(request):
    """Save (or re-bind after a new sign-in) this browser's subscription."""
    if _impersonating(request):
        # An admin viewing an owner's dashboard must never start receiving that
        # owner's notifications in their own browser.
        return _no_store(JsonResponse({"ok": False, "error": "Not available while impersonating."}, status=403))
    if not webpush.enabled():
        return _no_store(JsonResponse({"ok": False, "error": "Push notifications are switched off."}, status=503))
    if not ratelimit.consume("push.subscribe", str(request.user.pk)).allowed:
        return _no_store(JsonResponse({"ok": False, "error": "Too many attempts. Try again later."}, status=429))
    data = _json_body(request)
    try:
        sub = webpush.save(request, data.get("subscription") or data)
    except ValueError as exc:
        return _no_store(JsonResponse({"ok": False, "error": str(exc)}, status=400))
    return _no_store(JsonResponse({"ok": True, "id": str(sub.pk), "device": sub.device_label}))


@login_required
@require_POST
def unsubscribe(request):
    """Forget this browser (by endpoint) or one listed device (by id)."""
    data = _json_body(request)
    qs = PushSubscription.objects.filter(user=request.user)
    endpoint = str(data.get("endpoint") or "").strip()
    raw_id = str(data.get("id") or "").strip()
    if endpoint:
        qs = qs.filter(endpoint_hash=webpush.endpoint_hash(endpoint))
    elif raw_id:
        try:
            qs = qs.filter(pk=uuid.UUID(raw_id))
        except ValueError:
            return _no_store(JsonResponse({"ok": True, "removed": 0}))
    else:
        return _no_store(JsonResponse({"ok": False, "error": "Nothing to remove."}, status=400))
    removed, _ = qs.delete()
    return _no_store(JsonResponse({"ok": True, "removed": removed}))


@login_required
@require_POST
def test(request):
    """Send a test popup to this browser right away."""
    if not ratelimit.consume("push.test", str(request.user.pk)).allowed:
        return _no_store(JsonResponse({"ok": False, "error": "Too many test notifications. Wait a few minutes."}, status=429))
    data = _json_body(request)
    sub = None
    try:
        sub = PushSubscription.objects.filter(user=request.user, pk=uuid.UUID(str(data.get("id") or ""))).first()
    except ValueError:
        sub = None
    if sub is None:
        return _no_store(JsonResponse(
            {"ok": False, "error": "Notifications aren't on in this browser yet. Turn them on first."},
            status=404,
        ))
    result = webpush.send_test(sub)
    if result == "sent":
        return _no_store(JsonResponse({"ok": True}))
    if result == "gone":
        return _no_store(JsonResponse(
            {"ok": False, "gone": True, "error": "This browser stopped accepting notifications. Turn them on again."},
            status=410,
        ))
    return _no_store(JsonResponse(
        {"ok": False, "error": "The browser's push service didn't accept the test. Try again in a minute."},
        status=502,
    ))
