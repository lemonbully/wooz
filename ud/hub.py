"""Business Hub delivery: a bell notification + an email to every recipient.

A post is delivered when it is saved in the admin (see
``custom_admin.admin.BusinessHubPostAdmin``), in a background task so a post to
every owner never ties up the admin request. Delivery is idempotent per owner
(:class:`ud.models.BusinessHubReceipt`): re-saving a post, or adding customers
to it later, only reaches owners who haven't been sent it yet.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.urls import get_script_prefix, reverse, set_script_prefix
from django.utils.html import strip_tags

from core import links
from core.hosts import Surface

logger = logging.getLogger("onewooz.audit")

_LEVEL = {"news": "info", "help": "info", "important": "warning"}


def hub_url(kind: str = "") -> str:
    """Absolute URL of the Business Hub page (optionally on one tab).

    Built without a request (it runs in a worker), so reverse against the app
    urlconf at the root prefix and point it at the app surface.
    """
    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        path = reverse("ud:business_hub", urlconf="onewooz.urls.app")
    finally:
        set_script_prefix(previous)
    url = links.surface_url(Surface.APP, path)
    return f"{url}?tab={kind}" if kind else url


def excerpt(html: str, limit: int = 280) -> str:
    """Plain-text preview of a post body, for the bell and the email."""
    text = " ".join(strip_tags(html or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def target_users(post):
    """Owners this post should reach: every active store owner, or the chosen
    customers (active ones only)."""
    User = get_user_model()
    if post.send_to_all:
        # Owners who deleted their account (accounts.deletion) are left out.
        return User.objects.filter(is_active=True, is_staff=False, account_deleted_at__isnull=True)
    return post.recipients.filter(is_active=True, account_deleted_at__isnull=True)


def deliver(post_id) -> int:
    """Notify + queue an email for every recipient not yet sent this post.
    Returns how many owners were newly reached."""
    from accounts import notify

    from .models import BusinessHubPost, BusinessHubReceipt
    from .tasks import send_hub_email

    post = BusinessHubPost.objects.filter(pk=post_id, is_published=True).first()
    if post is None:
        return 0

    url = hub_url(post.kind)
    preview = excerpt(post.description)
    reached = 0
    pending = target_users(post).exclude(business_hub_receipts__post=post).order_by("pk")
    for user in pending.iterator():
        try:
            receipt, created = BusinessHubReceipt.objects.get_or_create(post=post, user=user)
        except Exception:
            logger.exception("business hub receipt failed post=%s user=%s", post.pk, user.pk)
            continue
        if not created:
            continue  # a concurrent delivery already reached this owner
        notify.push(
            user,
            headline=f"{post.get_kind_display()}: {post.title}",
            description=preview,
            kind="admin",
            level=_LEVEL.get(post.kind, "info"),
            action_url=url,
        )
        if (user.email or "").strip():
            try:
                send_hub_email.delay(str(receipt.pk))
            except Exception:
                logger.exception("queueing business hub email failed receipt=%s", receipt.pk)
        reached += 1
    return reached


def send_email_for(receipt_id) -> bool:
    """Send the email for one delivery (idempotent)."""
    from accounts import emails

    from .models import BusinessHubReceipt

    receipt = (
        BusinessHubReceipt.objects.select_related("post", "user")
        .filter(pk=receipt_id, emailed=False)
        .first()
    )
    if receipt is None:
        return False
    post, user = receipt.post, receipt.user
    email = (user.email or "").strip()
    if not email or not post.is_published:
        return False
    sent = emails.send_business_hub_post(
        email,
        kind_label=post.get_kind_display(),
        kind=post.kind,
        title=post.title,
        preview=excerpt(post.description, limit=600),
        url=hub_url(post.kind),
        name=(getattr(user, "full_name", "") or "").strip(),
        has_video=post.has_video,
    )
    if sent:
        BusinessHubReceipt.objects.filter(pk=receipt.pk).update(emailed=True)
    return sent
