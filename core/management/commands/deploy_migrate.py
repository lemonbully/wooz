"""Apply database migrations safely on every deploy.

The web container runs this on start (see ``deploy/entrypoint.sh``). With several
web replicas that would normally race  two containers applying the same
migration at once corrupts the run or crash-loops a replica. So on MySQL we take
a **named advisory lock** (``GET_LOCK``) on a dedicated connection: exactly one
container migrates at a time, the rest wait and then find nothing to do
(migrations are idempotent). On SQLite (dev) there is a single writer, so we just
migrate directly.

This is the automatic counterpart to the one-off ``migrate`` role  running it
on web start means "forgot to migrate on deploy" cannot happen.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.deploy_lock import deploy_lock

# Held for the duration of one deploy's migration run.
LOCK_NAME = "onewooz_deploy_migrate"
# Long enough to outlast a slow migration on another replica, short enough not
# to wedge a boot forever if something is truly stuck.
LOCK_TIMEOUT_SECONDS = 300


class Command(BaseCommand):
    help = "Run migrations guarded by a DB advisory lock (safe across replicas)."

    def handle(self, *args, **options):
        # On SQLite (dev) the lock is a no-op: single writer, no race.
        with deploy_lock(LOCK_NAME, LOCK_TIMEOUT_SECONDS) as got:
            if not got:
                # Another replica is migrating (or the wait timed out). It will
                # have applied everything; don't race it.
                self.stdout.write(
                    "Migrate lock is held by another instance; skipping "
                    "(migrations are being applied elsewhere)."
                )
                return

            self.stdout.write("Acquired migrate lock; applying migrations…")
            call_command("migrate", "--noinput")
            self.stdout.write(self.style.SUCCESS("Migrations applied."))
