"""Default media storage for production: S3, plus automatic video compression.

Every upload in the project reaches S3 through ``default_storage`` (FileField
saves on any model, and the editor/marketing code that calls
``default_storage.save`` directly), so this is the one place that sees every
new video. After a video is saved, a background job re-encodes it in place
(commerce.video_optimise): same key, same URL, a fraction of the bytes.

Owners upload phone videos that average about 60 MB; served as-is, each play
costs that much CDN transfer. The job brings them to roughly 4 MB per 30 s at
720p with no visible change on a phone screen.

The upload itself is never slowed or put at risk: the job is queued only after
the surrounding database transaction commits, and any failure to queue is
logged and ignored (the original file simply stays as uploaded).
"""

from __future__ import annotations

import logging

from django.db import transaction
from storages.backends.s3 import S3Storage

logger = logging.getLogger("onewooz.media")

#: Containers the job re-encodes. WebM/Ogg are left alone: templates may declare
#: their own type for them, and they are rare.
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov")


def is_optimisable_video(name: str) -> bool:
    return (name or "").lower().rsplit("?", 1)[0].endswith(VIDEO_EXTENSIONS)


def queue_video_optimisation(name: str) -> None:
    """Queue the in-place re-encode for ``name`` once the transaction commits."""

    def _send():
        try:
            from commerce.tasks import optimise_video

            optimise_video.delay(name)
        except Exception:  # noqa: BLE001 - never break an upload over this
            logger.warning("could not queue video optimisation for %s", name, exc_info=True)

    try:
        transaction.on_commit(_send)
    except Exception:  # noqa: BLE001
        _send()


class MediaStorage(S3Storage):
    """S3Storage that queues video compression for every newly saved video."""

    def _save(self, name, content):
        saved = super()._save(name, content)
        if is_optimisable_video(saved):
            queue_video_optimisation(saved)
        return saved
