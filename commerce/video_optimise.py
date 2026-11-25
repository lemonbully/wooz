"""Re-encode an uploaded video in place on S3 (run by the ``media`` worker).

Steps for one object key:

1. Skip it if it was already processed (S3 metadata ``onewooz-optimised``), or
   if it is already small (``VIDEO_OPTIMISE_MIN_BYTES``).
2. Download it to a temporary file and run ffmpeg: H.264 + AAC in an MP4
   container, longest side at most 1280 px (portrait reels stay portrait),
   CRF 26 with a 2.5 Mbit/s ceiling, ``faststart`` so playback begins before
   the whole file arrives.
3. Keep the result only if it is meaningfully smaller (under 90% of the
   original). Either way the object is marked processed, so it is never
   retried in a loop.
4. Copy the original to ``originals/<key>`` (an undo path), then overwrite the
   key with the new bytes: every page, product and email that links to the
   video keeps working and simply loads the small file.
5. Refresh the media library row's size/type, and ask CloudFront to drop its
   cached copy of that one path.

Requires the ``ffmpeg`` binary (installed in deploy/Dockerfile). Without it the
job logs once and leaves the video untouched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time

from django.conf import settings

logger = logging.getLogger("onewooz.media")

MARKER = "onewooz-optimised"
ORIGINALS_PREFIX = "originals/"


def _setting(name: str, default):
    return getattr(settings, name, default)


def _s3():
    import boto3

    return boto3.client("s3", region_name=_setting("AWS_S3_REGION_NAME", "ap-south-1"))


def _bucket() -> str:
    return settings.AWS_STORAGE_BUCKET_NAME


def _ffmpeg_args(src: str, dst: str) -> list[str]:
    max_side = int(_setting("VIDEO_OPTIMISE_MAX_SIDE", 1280))
    crf = str(_setting("VIDEO_OPTIMISE_CRF", 26))
    maxrate = str(_setting("VIDEO_OPTIMISE_MAXRATE", "2500k"))
    # Scale the longer side down to max_side, never up; keep even dimensions.
    scale = (
        f"scale='if(gte(iw,ih),min({max_side},iw),-2)':'if(gte(iw,ih),-2,min({max_side},ih))'"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", scale,
        # 50/60 fps phone and stock footage gains nothing visible over 30 fps on
        # a storefront; lower-rate sources are left as they are.
        "-fpsmax", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
        "-maxrate", maxrate, "-bufsize", "5000k",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ]


def _invalidate_cdn(key: str) -> None:
    dist = _setting("MEDIA_CDN_DISTRIBUTION_ID", "")
    if not dist:
        return
    try:
        import boto3

        boto3.client("cloudfront").create_invalidation(
            DistributionId=dist,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/" + key.lstrip("/")]},
                "CallerReference": f"video-{key}-{time.time():.0f}"[:128],
            },
        )
    except Exception:  # noqa: BLE001 - the edge copy expires on its own anyway
        logger.warning("CDN invalidation failed for %s", key, exc_info=True)


def _refresh_library_row(key: str, size: int) -> None:
    """Keep commerce.MediaAsset's stored size/type truthful after the swap."""
    try:
        from commerce.models import MediaAsset

        # Routing by storage key, before any store is known: the key is unique
        # across the platform.
        MediaAsset.objects.across_all_sites().filter(file=key).update(byte_size=size, mime_type="video/mp4")
    except Exception:  # noqa: BLE001
        logger.warning("could not refresh media library row for %s", key, exc_info=True)


def optimise(key: str) -> str:
    """Re-encode one S3 object in place. Returns a short outcome label."""
    key = (key or "").lstrip("/")
    if not key or key.startswith(ORIGINALS_PREFIX):
        return "skipped:key"
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg is not installed; leaving %s as uploaded", key)
        return "skipped:no-ffmpeg"

    s3, bucket = _s3(), _bucket()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception:  # noqa: BLE001 - deleted before the job ran
        return "skipped:missing"
    meta = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
    if MARKER in meta:
        return "skipped:done"
    size = int(head.get("ContentLength") or 0)
    min_bytes = int(_setting("VIDEO_OPTIMISE_MIN_BYTES", 8 * 1024 * 1024))

    cache_control = head.get("CacheControl") or "public, max-age=604800"

    def mark(extra: dict | None = None):
        # Copy onto itself to add the marker without touching the bytes.
        s3.copy_object(
            Bucket=bucket, Key=key, CopySource={"Bucket": bucket, "Key": key},
            MetadataDirective="REPLACE", Metadata={**meta, MARKER: "1", **(extra or {})},
            ContentType=head.get("ContentType") or "video/mp4", CacheControl=cache_control,
        )

    if size and size <= min_bytes:
        mark({"onewooz-result": "small"})
        return "skipped:small"

    with tempfile.TemporaryDirectory(prefix="ow-video-") as tmp:
        src = os.path.join(tmp, "in" + os.path.splitext(key)[1].lower())
        dst = os.path.join(tmp, "out.mp4")
        s3.download_file(bucket, key, src)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                _ffmpeg_args(src, dst), capture_output=True, text=True,
                timeout=int(_setting("VIDEO_OPTIMISE_TIMEOUT", 840)),
            )
        except subprocess.TimeoutExpired:
            # Marked so it is not queued again and again; the original stays.
            logger.warning("ffmpeg timed out for %s (%.0f MB); keeping the original", key, size / 1e6)
            mark({"onewooz-result": "timeout"})
            return "failed:timeout"
        if proc.returncode != 0 or not os.path.exists(dst):
            logger.warning("ffmpeg failed for %s: %s", key, (proc.stderr or "")[-500:])
            mark({"onewooz-result": "ffmpeg-failed"})
            return "failed:ffmpeg"
        new_size = os.path.getsize(dst)
        if not size or new_size >= size * 0.9:
            mark({"onewooz-result": "kept-original"})
            return "kept:original-smaller"

        # Undo path first, then the swap.
        s3.copy_object(Bucket=bucket, Key=ORIGINALS_PREFIX + key, CopySource={"Bucket": bucket, "Key": key})
        s3.upload_file(
            dst, bucket, key,
            ExtraArgs={
                "ContentType": "video/mp4",
                "CacheControl": cache_control,
                "Metadata": {MARKER: "1", "onewooz-original-bytes": str(size)},
            },
        )
        logger.info(
            "video %s: %.1f MB -> %.1f MB in %.0fs", key, size / 1e6, new_size / 1e6, time.monotonic() - started
        )

    _refresh_library_row(key, new_size)
    _invalidate_cdn(key)
    return "optimised"
