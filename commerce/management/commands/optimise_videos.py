"""Queue compression for videos uploaded before automatic compression existed.

    python manage.py optimise_videos            # list what would be queued
    python manage.py optimise_videos --queue    # queue them (media worker runs them)

Lists every .mp4/.m4v/.mov object in the uploads bucket (skipping the
``originals/`` backups), skips ones already marked processed, and queues
commerce.tasks.optimise_video for the rest. Safe to re-run: processed videos
are marked on S3 and skipped.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Queue in-place compression for existing uploaded videos."

    def add_arguments(self, parser):
        parser.add_argument("--queue", action="store_true", help="Actually queue the jobs (default: dry run).")

    def handle(self, *args, **opts):
        import boto3

        from commerce.media_storage import is_optimisable_video
        from commerce.tasks import optimise_video
        from commerce.video_optimise import MARKER, ORIGINALS_PREFIX

        s3 = boto3.client("s3", region_name=getattr(settings, "AWS_S3_REGION_NAME", "ap-south-1"))
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        found = queued = done = 0
        total_bytes = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.startswith(ORIGINALS_PREFIX) or not is_optimisable_video(key):
                    continue
                found += 1
                meta = s3.head_object(Bucket=bucket, Key=key).get("Metadata") or {}
                if MARKER in {k.lower() for k in meta}:
                    done += 1
                    continue
                total_bytes += obj["Size"]
                self.stdout.write(f"{obj['Size'] / 1e6:8.1f} MB  {key}")
                if opts["queue"]:
                    optimise_video.delay(key)
                    queued += 1
        self.stdout.write(
            f"\n{found} videos, {done} already processed, "
            f"{found - done} to process ({total_bytes / 1e6:.0f} MB). "
            + (f"Queued {queued}." if opts["queue"] else "Dry run: add --queue to queue them.")
        )
