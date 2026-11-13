"""Abstract base models and the audit trail.

Every concrete model in OneWooz inherits from :class:`BaseModel`, and every
model that belongs to a tenant inherits from :class:`TenantScopedModel`.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from .ids import uuid7
from .managers import SiteScopedManager

__all__ = [
    "TimeStampedModel",
    "BaseModel",
    "TenantScopedModel",
    "SoftDeleteMixin",
    "AuditEvent",
]


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class BaseModel(TimeStampedModel):
    """UUIDv7 primary key plus timestamps.

    The key is generated in Python rather than by the database so that related
    objects can be built and linked in memory before a single INSERT is issued,
    and so an object's identifier is stable from the moment it is constructed.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.pk}>"


class SoftDeleteMixin(models.Model):
    """Reversible deletion for records an owner can remove from the UI.

    Used where a hard delete would orphan history that must survive  deleting
    a product must not break the line items of orders that already contain it.
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def soft_delete(self, *, save: bool = True) -> None:
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if save:
            self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def restore(self, *, save: bool = True) -> None:
        self.is_deleted = False
        self.deleted_at = None
        if save:
            self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class TenantScopedModel(BaseModel):
    """Base for any model owned by exactly one site.

    Subclasses must declare the foreign key themselves::

        class Product(TenantScopedModel):
            site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE,
                                     related_name="products")

    The field is not declared here so that ``core`` carries no dependency on
    ``tenancy``. A system check (``core.checks``) verifies every subclass
    actually defines it, so a missing key is caught at startup rather than at
    the first query.
    """

    #: Name of the foreign key pointing at the owning site.
    SITE_FIELD = "site"

    objects = SiteScopedManager()

    # Used by Django internals  related-object fetching, migrations,
    # ``dumpdata``  which must not be subject to tenant scoping.
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"


class AuditEvent(BaseModel):
    """Append-only record of consequential actions.

    Written for anything that changes money, access, or another person's data:
    admin actions, impersonation, credential changes, plan changes, domain
    verification, refunds. Rows are never updated and never deleted by
    application code  the table is the evidence.

    Actor and target are stored denormalised as strings rather than as foreign
    keys, so the trail survives deletion of the account it describes. Losing an
    admin's audit history because the admin account was removed would defeat
    the point.
    """

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        NOTICE = "notice", "Notice"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    # What happened. Dotted namespace, e.g. "admin.impersonation.start".
    action = models.CharField(max_length=100, db_index=True)
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.INFO, db_index=True
    )

    # Who did it.
    actor_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    actor_label = models.CharField(max_length=255, blank=True, default="")
    actor_kind = models.CharField(
        max_length=20,
        default="user",
        help_text="user, admin, system, customer or anonymous",
    )

    # What it happened to.
    target_kind = models.CharField(max_length=64, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default="")

    # Which tenant it concerned, if any. A plain id, not a foreign key, for the
    # same reason as the actor.
    site_id_ref = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # Request context.
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    request_id = models.CharField(max_length=36, blank=True, default="", db_index=True)
    surface = models.CharField(max_length=20, blank=True, default="")

    # Before/after for changes, plus anything else worth keeping. Must never
    # contain secrets: core.audit strips known-sensitive keys before writing.
    changes = models.JSONField(default=dict, blank=True)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "audit event"
        verbose_name_plural = "audit events"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("action", "-created_at")),
            models.Index(fields=("actor_id", "-created_at")),
            models.Index(fields=("site_id_ref", "-created_at")),
            models.Index(fields=("target_kind", "target_id", "-created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor_label or self.actor_kind}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit events are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit events are append-only and cannot be deleted.")
