"""When each dashboard user last opened each counted sidebar page.

The store sidebar shows an unread counter beside Orders, Requests, Reviews,
Subscribers, Out of stock, Collaborations and Bills: how many new items arrived
since *this* user last opened that page (see :mod:`commerce.nav_counts`). One
row per (store, user, page); the owner and every staff member keep their own.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class NavSeen(TenantScopedModel):
    """The moment ``user`` last opened sidebar page ``key`` of ``site``."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="nav_seen"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    #: Sidebar key (``commerce.context.NAV_GROUPS``), e.g. ``"orders"``.
    key = models.CharField(max_length=32)
    seen_at = models.DateTimeField()
    #: Out of stock only: the sold-out rows (``"<product id>:<variation>"``)
    #: on screen at that visit, so only newly sold-out ones are counted.
    seen_refs = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("site", "user", "key"), name="uniq_nav_seen_per_user_page"
            )
        ]

    def __str__(self) -> str:
        return f"{self.key} · {self.seen_at:%Y-%m-%d %H:%M}"
