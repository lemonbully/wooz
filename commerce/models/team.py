"""Store team: staff/admin members an owner adds to help run a store.

A store owner can grant other people scoped access to their store dashboard. Each
:class:`StaffMember` is one such grant  identified by the person's email, with
a per-page set of allowed actions (view / create / edit / delete). The person
signs in with their own OneWooz account; the membership is linked to that account
by email the first time they open the store (see :func:`commerce.staff.access_for`).

Permissions are stored as ``{page_key: ["view", "create", …]}`` where the page
keys are the store sidebar keys (``commerce.context.NAV_GROUPS`` / the catalogue
in :mod:`commerce.staff`). A staff member only ever sees the pages they are
granted, and is blocked (nav + route) from everything else  the whole thing is
enforced structurally by :class:`commerce.middleware.StaffAccessGateMiddleware`.

The number of staff an owner may add is capped by their subscription's
``number_of_collaborations`` grant (the "team collaborators" line on a plan).
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import SoftDeleteMixin, TenantScopedModel


class StaffMember(TenantScopedModel, SoftDeleteMixin):
    """One person granted scoped access to a store's dashboard."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="staff_members"
    )
    # Linked to the person's account the first time they sign in and open the
    # store; matched by email until then. Nullable so an owner can add someone
    # before that person has ever signed in.
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_memberships",
    )
    email = models.EmailField(db_index=True)
    name = models.CharField(max_length=120, blank=True, default="")

    #: ``{page_key: ["view", "create", "edit", "delete"]}``  the actions this
    #: member may perform on each store page. A page absent (or with an empty
    #: list) is not accessible to them at all.
    permissions = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    invited_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "staff member"
        verbose_name_plural = "staff members"
        constraints = [
            models.UniqueConstraint(
                fields=("site", "email"),
                condition=models.Q(is_deleted=False),
                name="uniq_staff_email_per_site",
            )
        ]
        indexes = [
            models.Index(fields=("site", "is_active")),
            models.Index(fields=("user",)),
        ]

    def __str__(self) -> str:
        return f"{self.name or self.email} @ {self.site_id}"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return self.name or self.email

    def allowed_pages(self) -> set[str]:
        """The page keys this member may open (those with at least one action)."""
        perms = self.permissions if isinstance(self.permissions, dict) else {}
        return {k for k, v in perms.items() if v}
