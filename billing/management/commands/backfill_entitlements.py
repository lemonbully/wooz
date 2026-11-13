"""Seed :class:`~billing.models.AccountEntitlement` for owners who settled a
subscription before the snapshot existed.

For every email with a settled (PAID) order, this picks that owner's *current*
subscription the same way :func:`billing.subscriptions.current_for_owner` does 
the order still inside its period, or failing that the most recent one  and
freezes a snapshot from **that order's live plan**. After it runs, editing a plan
no longer moves those owners.

Idempotent by default: an email that already has a snapshot is left untouched, so
re-running is safe. Pass ``--force`` to re-snapshot everyone from their current
live plan (e.g. after deliberately changing what a plan grants and wanting the
change to land on existing owners).

    python manage.py backfill_entitlements
    python manage.py backfill_entitlements --force
    python manage.py backfill_entitlements --dry-run

Following the project's working agreement, this only reads and writes rows; it is
the user who runs it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from billing import entitlements
from billing.models import AccountEntitlement, SubscriptionOrder


class Command(BaseCommand):
    help = "Freeze an AccountEntitlement snapshot for every owner who has a settled subscription."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-snapshot even emails that already have an entitlement.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        dry_run = options["dry_run"]

        existing = set(
            AccountEntitlement.objects.values_list("email", flat=True)
        )

        # Every settled order that still points at a plan, most-current first per
        # owner: an order inside its period (largest period_end) wins, else the
        # most recently created. Iterating in this order lets us keep the first
        # order we see for each email.
        orders = (
            SubscriptionOrder.objects.filter(
                status=SubscriptionOrder.Status.PAID,
                plan__isnull=False,
            )
            .select_related("plan")
            .order_by("customer_email", "-current_period_end", "-created_at")
        )

        chosen: dict[str, SubscriptionOrder] = {}
        for order in orders:
            key = (order.customer_email or "").strip().lower()
            if not key or key in chosen:
                continue
            chosen[key] = order

        created = skipped = 0
        for email, order in sorted(chosen.items()):
            if email in existing and not force:
                skipped += 1
                continue
            if dry_run:
                created += 1
                self.stdout.write(f"  would snapshot {email} → {order.plan.name}")
                continue
            ent = entitlements.grant_from_order(order)
            if ent is None:
                skipped += 1
                continue
            created += 1
            self.stdout.write(f"  snapshot {email} → {order.plan.name}")

        verb = "would write" if dry_run else "wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {verb} {created} entitlement(s); skipped {skipped}."
            )
        )
