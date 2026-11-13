"""Collect static files safely on every deploy.

Production static files live in one shared S3 bucket behind a manifest storage
that deletes and rewrites hashed copies during post-processing. When two web
replicas boot together (every rolling deploy starts two), both ran
``collectstatic`` against the same bucket at once: one replica deleted a file
the other had just listed, the other's ``HeadObject`` got a 404, the command
raised, the container exited 1, and ECS kept stopping and replacing the task.

This takes a cross-replica advisory lock (``core.deploy_lock``) so exactly one
replica collects at a time; the next finds every file unmodified and finishes in
seconds. If the lock wait ever times out, the replica skips collecting rather
than racing: the files in S3 are shared, so the replica holding the lock is
publishing them for everyone.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.deploy_lock import deploy_lock

LOCK_NAME = "onewooz_deploy_collectstatic"
# Long enough to outlast a full upload + minify on another replica.
LOCK_TIMEOUT_SECONDS = 600


class Command(BaseCommand):
    help = "Run collectstatic guarded by a DB advisory lock (safe across replicas)."

    def handle(self, *args, **options):
        with deploy_lock(LOCK_NAME, LOCK_TIMEOUT_SECONDS) as got:
            if not got:
                self.stdout.write(
                    "collectstatic lock is held by another instance; skipping "
                    "(static files are being published elsewhere)."
                )
                return
            call_command("collectstatic", "--noinput")
