"""Create (or refresh) the local demo store, its demo category and subcategory.

Runs automatically after ``migrate`` in development (see
``commerce.apps.CommerceConfig.ready``); this command lets you re-run it by hand.
It refuses to do anything unless ``DEBUG`` is on, so it can never touch a live
database.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from core import dev_seed


class Command(BaseCommand):
    help = "Seed the local demo store with a demo category and subcategory (DEBUG only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING("DEBUG is off  refusing to seed. No changes made."))
            return

        site = dev_seed.seed_dev_store(log=lambda m: self.stdout.write(f"  {m}"))
        if site is None:
            self.stdout.write(self.style.WARNING("Skipped (not in DEBUG)."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Demo store ready. Sign in as {dev_seed.DEMO_OWNER_EMAIL} "
            f"(password: {dev_seed.DEMO_OWNER_PASSWORD})."
        ))
