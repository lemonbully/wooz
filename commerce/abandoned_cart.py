"""Abandoned cart reminder emails (D2C, opt-in per store).

When the owner ticks **Send abandoned cart emails** (Store settings, or the
same switch on the Products page), a *signed-in* shopper who leaves items in
their cart without checking out is emailed a reminder at five points after
their last change to the cart:

    1 hour, 6 hours, 24 hours, 48 hours, 72 hours

and never after the 72-hour one. The cart itself lives in the shopper's
session (:class:`commerce.cart.Cart`); every change a signed-in shopper makes
is mirrored into one :class:`commerce.models.AbandonedCart` row per shopper per
store, which is what the scheduler reads.

How a sequence ends:

* **The shopper responds**: clicking a reminder's "Return to your cart" link
  (or its "Stop these reminders" link) ends the sequence at once. Nothing more
  is sent until the shopper changes their cart again and leaves it again, which
  starts a fresh sequence from that change.
* **They buy**: a placed (or paid) order after their last cart change ends it.
* **The cart empties**: removing every item, or the products going away.
* **The 72-hour limit**: after the fifth reminder the row is ``exhausted``.

Any later change to the cart (add, quantity, remove, or signing in with items
in the cart) restarts the timer from that moment, so a shopper who is still
browsing is never reminded mid-visit.

Celery Beat runs :func:`send_due_reminders` every few minutes. Each reminder
is claimed with a conditional UPDATE before it is mailed, so two workers can
never send the same one twice, and a shopper who touches their cart while the
task is running is never mailed against the stale cart.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from core import tokens

logger = logging.getLogger(__name__)

#: When each reminder goes out, measured from the shopper's last cart change.
REMINDER_OFFSETS = (
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),
    timedelta(hours=48),
    timedelta(hours=72),
)
#: A reminder found more than this late (the worker was down, the owner had the
#: switch off for a while) is not sent at all once the 72-hour window is over:
#: an email days after the fact reads as spam, not as a reminder.
_LATE_GRACE = timedelta(hours=6)

TOKEN_PURPOSE = "commerce.cart_recovery"
ACTION_OPEN = "open"
ACTION_STOP = "stop"

# Per reminder: (subject, kicker, heading, lead). ``{store}`` is the store name.
_COPY = (
    (
        "You left something in your cart at {store}",
        "Your cart",
        "You left something behind",
        "Your cart at {store} is saved. Pick up right where you left off.",
    ),
    (
        "Your cart at {store} is waiting",
        "Still thinking?",
        "Your picks are waiting",
        "We've kept everything in your cart at {store}, ready when you are.",
    ),
    (
        "Still interested? Your {store} cart is saved",
        "Saved for you",
        "Your cart is still saved",
        "The items you chose at {store} are still in your cart. Checkout takes a minute.",
    ),
    (
        "Don't miss out on your {store} picks",
        "A quick reminder",
        "Still want these?",
        "Stock moves, so your items at {store} may not wait forever.",
    ),
    (
        "Last reminder: your cart at {store}",
        "Last reminder",
        "One last reminder",
        "This is the last time we'll remind you about your cart at {store}.",
    ),
)


# --------------------------------------------------------------------------
# Who gets reminders
# --------------------------------------------------------------------------
def enabled_for(site) -> bool:
    """The store sells to consumers and the owner switched reminders on."""
    return bool(
        site is not None
        and getattr(site, "is_d2c", False)
        and getattr(site, "abandoned_cart_emails_enabled", False)
        and not getattr(site, "is_deleted", False)
    )


def _shopper_email(request) -> str:
    from storefront.customer_accounts import current_customer_email

    try:
        return current_customer_email(request)
    except Exception:
        return ""


def _trackable(request, site) -> str:
    """The signed-in shopper's email when this cart should be tracked, else "".

    Collaborator (UGC) stores are left out: a reminder links back to the
    owner's own store, which would take the sale away from the creator.
    """
    if request is None or getattr(request, "is_referral", False):
        return ""
    if not enabled_for(site):
        return ""
    return _shopper_email(request)


# --------------------------------------------------------------------------
# Mirroring the session cart (called by commerce.cart.Cart)
# --------------------------------------------------------------------------
def record_activity(request, site, data: dict) -> None:
    """The shopper changed their cart: store it and (re)start the sequence.

    An empty cart ends any running sequence instead. Never raises: a database
    hiccup here must not break adding to the cart.
    """
    email = _trackable(request, site)
    if not email:
        return
    from commerce.models import AbandonedCart

    now = timezone.now()
    try:
        if not data:
            AbandonedCart.objects.for_site(site).filter(customer_email=email).update(
                items={}, status=AbandonedCart.Status.EMPTY,
                next_reminder_at=None, updated_at=now,
            )
            return
        AbandonedCart.objects.for_site(site).update_or_create(
            site=site,
            customer_email=email,
            defaults={
                "items": dict(data),
                "status": AbandonedCart.Status.ACTIVE,
                "last_activity_at": now,
                "reminders_sent": 0,
                "last_reminder_at": None,
                "next_reminder_at": now + REMINDER_OFFSETS[0],
            },
        )
    except Exception:
        logger.exception("abandoned cart: recording activity failed site=%s", site.pk)


def mark_converted(request, site) -> None:
    """The cart was checked out (``Cart.clear`` after a placed order)."""
    email = _shopper_email(request) if request is not None else ""
    if not email or site is None:
        return
    from commerce.models import AbandonedCart

    try:
        AbandonedCart.objects.for_site(site).filter(customer_email=email).update(
            items={}, status=AbandonedCart.Status.CONVERTED,
            next_reminder_at=None, updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("abandoned cart: marking converted failed site=%s", site.pk)


# --------------------------------------------------------------------------
# Links in the email
# --------------------------------------------------------------------------
def _link(row, action: str) -> str:
    from storefront.hosted_pay import _owner_path, owner_origin

    token = tokens.sign(TOKEN_PURPOSE, {"c": str(row.pk), "s": str(row.site_id), "a": action})
    name = "public_cart_recover" if action == ACTION_OPEN else "public_cart_reminders_stop"
    return owner_origin(row.site) + _owner_path(name, token=token)


def read_link(site, token: str, action: str):
    """The AbandonedCart a reminder link points at, or None when the token is
    bad, expired, for another action, or for another store."""
    from commerce.models import AbandonedCart

    try:
        payload = tokens.unsign(TOKEN_PURPOSE, token)
    except tokens.TokenError:
        return None
    if payload.get("a") != action or payload.get("s") != str(site.pk):
        return None
    return AbandonedCart.objects.for_site(site).filter(pk=payload.get("c")).first()


def respond(row, *, stop: bool = False) -> None:
    """The shopper acted on a reminder: end the sequence.

    A finished state (bought, emptied, already stopped) is left as it is; a
    running or exhausted one becomes ``responded`` (or ``stopped``), and only a
    later change to the cart starts reminders again.
    """
    from commerce.models import AbandonedCart

    S = AbandonedCart.Status
    open_states = (S.ACTIVE, S.EXHAUSTED, S.RESPONDED)
    if row.status not in open_states:
        return
    now = timezone.now()
    AbandonedCart.all_objects.filter(pk=row.pk, status__in=open_states).update(
        status=S.STOPPED if stop else S.RESPONDED,
        next_reminder_at=None,
        responded_at=now,
        updated_at=now,
    )


def restore_into(cart, row) -> None:
    """Put the reminder's items back into ``cart`` (lines already there are
    kept as they are). Written straight to the session, not through
    ``Cart.add``, so restoring does not count as a new cart change and
    restart the reminders the shopper just responded to."""
    from commerce.models import AbandonedCart

    if row.status == AbandonedCart.Status.CONVERTED or not isinstance(row.items, dict):
        return
    merged = dict(cart.data)
    for key, line in cart._normalize(row.items).items():
        merged.setdefault(key, line)
    if merged != cart.data:
        cart.data = merged
        cart._save()


# --------------------------------------------------------------------------
# Sending (Celery Beat)
# --------------------------------------------------------------------------
def send_due_reminders(limit: int = 500) -> int:
    """Send every reminder that is due now. Returns how many were sent."""
    from commerce.models import AbandonedCart

    now = timezone.now()
    # Keys only and unsorted: no ORDER BY over wide rows (MySQL error 1038).
    due = list(
        AbandonedCart.all_objects.filter(
            status=AbandonedCart.Status.ACTIVE, next_reminder_at__lte=now
        ).values_list("pk", flat=True)[:limit]
    )
    sent = 0
    for pk in due:
        try:
            if _process(pk, now):
                sent += 1
        except Exception:
            logger.exception("abandoned cart: reminder failed row=%s", pk)
    return sent


def _end(row, status, now) -> None:
    from commerce.models import AbandonedCart

    AbandonedCart.all_objects.filter(
        pk=row.pk, status=AbandonedCart.Status.ACTIVE, last_activity_at=row.last_activity_at
    ).update(status=status, next_reminder_at=None, updated_at=now)


def _process(pk, now) -> bool:
    from commerce.models import AbandonedCart, Order

    S = AbandonedCart.Status
    row = AbandonedCart.all_objects.select_related("site").filter(pk=pk).first()
    if row is None or row.status != S.ACTIVE or row.next_reminder_at is None or row.next_reminder_at > now:
        return False
    site = row.site

    if not enabled_for(site):
        # Switched off (or no longer D2C): end quietly. The next cart change
        # after it is switched back on starts a fresh sequence.
        _end(row, S.ACTIVE, now)
        return False
    if getattr(site, "maintenance_mode", False):
        # The store is closed: a link to it would land on the maintenance page.
        # Try again later; the 72-hour limit still applies.
        AbandonedCart.all_objects.filter(pk=row.pk, status=S.ACTIVE).update(
            next_reminder_at=now + timedelta(hours=1), updated_at=now
        )
        return False

    # Bought since the last cart change (any placed order; an unpaid online
    # checkout is not an order yet).
    if (
        Order.all_objects.filter(
            site=site, customer_email__iexact=row.customer_email,
            created_at__gte=row.last_activity_at,
        )
        .exclude(status=Order.Status.AWAITING_PAYMENT)
        .exists()
    ):
        _end(row, S.CONVERTED, now)
        return False

    deadline = row.last_activity_at + REMINDER_OFFSETS[-1]
    if now > deadline + _LATE_GRACE:
        _end(row, S.EXHAUSTED, now)
        return False

    lines = _lines(site, row.items)
    if not lines:
        _end(row, S.EMPTY, now)
        return False

    # The latest window that has opened. Normally this is the next one in
    # turn; after an outage it skips the missed ones rather than sending a burst.
    step = max(i for i, off in enumerate(REMINDER_OFFSETS) if row.last_activity_at + off <= now)
    step = max(step, row.reminders_sent)
    sent_after = step + 1
    done = sent_after >= len(REMINDER_OFFSETS)
    claimed = AbandonedCart.all_objects.filter(
        pk=row.pk,
        status=S.ACTIVE,
        reminders_sent=row.reminders_sent,
        last_activity_at=row.last_activity_at,
    ).update(
        reminders_sent=sent_after,
        last_reminder_at=now,
        next_reminder_at=None if done else row.last_activity_at + REMINDER_OFFSETS[sent_after],
        status=S.EXHAUSTED if done else S.ACTIVE,
        updated_at=now,
    )
    if not claimed:
        return False  # another worker took it, or the shopper just changed the cart
    return _send(row, step, lines)


def _money(value) -> Decimal:
    from commerce.cart import _money as cart_money

    return cart_money(value)


def _absolute(url: str, origin: str) -> str:
    if url and url.startswith("/"):
        return origin + url
    return url or ""


def _lines(site, items) -> list[dict]:
    """The stored cart lines resolved against live products, priced like the
    cart prices them. Lines whose product is gone or inactive are dropped."""
    from commerce import cart as cart_mod
    from commerce.models import Product, ProductImage
    from storefront.hosted_pay import owner_origin

    if not isinstance(items, dict) or not items:
        return []
    raw = []
    for line in items.values():
        if isinstance(line, dict) and line.get("pid"):
            try:
                qty = int(line.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                raw.append((str(line["pid"]), str(line.get("vk") or ""), cart_mod.clean_attrs(line.get("attrs")), qty))
    if not raw:
        return []
    products = {
        str(p.pk): p
        for p in Product.objects.for_site(site).filter(
            pk__in={r[0] for r in raw}, is_active=True, is_deleted=False
        )
    }
    if not products:
        return []

    origin = owner_origin(site)
    images = {}
    for img in ProductImage.objects.for_site(site).filter(product_id__in=list(products)).only(
        "product_id", "image", "order"
    ):
        pid = str(img.product_id)
        if pid not in images or (img.order or 0) < (images[pid].order or 0):
            images[pid] = img

    out = []
    for pid, vk, attrs, qty in raw:
        product = products.get(pid)
        if product is None:
            continue
        combo = cart_mod._combo_for(product, vk, attrs) if vk else None
        label_combo = combo if combo is not None else ({"attributes": attrs} if attrs else None)
        unit = _money(cart_mod._variant_price(product, combo))
        image_url = ""
        img = images.get(pid)
        if img is not None and img.image:
            try:
                image_url = _absolute(img.image.url, origin)
            except Exception:
                image_url = ""
        try:
            product_url = origin + _product_path(product)
        except Exception:
            product_url = ""
        out.append({
            "title": product.title,
            "variant_label": cart_mod._variant_label(label_combo),
            "quantity": qty,
            "unit_price": unit,
            "line_total": _money(unit * qty),
            "image_url": image_url,
            "product_url": product_url,
        })
    return out


def _product_path(product) -> str:
    from storefront.hosted_pay import _owner_path

    return _owner_path("public_product_detail", product_slug=product.slug)


def _send(row, step: int, lines: list[dict]) -> bool:
    site = row.site
    to = (row.customer_email or "").strip()
    if not to:
        return False
    store = (getattr(site, "name", "") or "").strip() or "our store"

    from core.mail import store_mail_connection
    from storefront.templatetags.website_filters import currency_symbol

    subject, kicker, heading, lead = (s.format(store=store) for s in _COPY[step])
    open_url = _link(row, ACTION_OPEN)
    stop_url = _link(row, ACTION_STOP)
    context = {
        "brand": store,
        "kicker": kicker,
        "heading": heading,
        "lead": lead,
        "items": lines,
        "sym": currency_symbol(getattr(site, "currency", "INR")),
        "subtotal": _money(sum((ln["line_total"] for ln in lines), Decimal("0"))),
        "cart_url": open_url,
        "stop_url": stop_url,
        "is_last": step == len(REMINDER_OFFSETS) - 1,
        "reply_to": bool(getattr(site, "support_email", "")),
        "powered_by": True,
    }
    try:
        text_body = render_to_string("commerce/email/abandoned_cart.txt", context)
    except Exception:
        logger.exception("abandoned cart: text render failed row=%s", row.pk)
        return False
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=site.customer_from_header,
        to=[to],
        reply_to=[site.support_email] if getattr(site, "support_email", "") else None,
        headers={"List-Unsubscribe": f"<{stop_url}>"},
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        message.attach_alternative(
            render_to_string("commerce/email/abandoned_cart.html", context), "text/html"
        )
    except Exception:
        logger.exception("abandoned cart: html render failed row=%s", row.pk)
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("abandoned cart: delivery failed row=%s", row.pk)
        return False
    return True
