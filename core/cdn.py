"""Route every S3 object URL through the CDN.

Storage emits CDN URLs once ``ONEWOOZ_MEDIA_CDN_DOMAIN`` /
``ONEWOOZ_STATIC_CDN_DOMAIN`` are set, but absolute URLs written *before* the
CDN existed are frozen in stored data: editor uploads persist
``default_storage.url(path)`` into ``Site.content``, media-library rows, variation
images, blog HTML. Those still point straight at
``<bucket>.s3.<region>.amazonaws.com`` and bypass the edge cache.

Both buckets sit behind one distribution whose paths mirror the object keys
(``/static/*`` → static bucket, everything else → uploads bucket), so a rewrite
is a pure host swap: the path, and therefore the object, is unchanged.

* :func:`cdn_url` rewrites one URL (template filters, JSON feeds).
* :func:`rewrite_text` rewrites every occurrence in a body (the response-level
  safety net in ``core.middleware.EdgeDeliveryMiddleware``), including the
  ``https:\\/\\/`` form some JSON encoders emit.

Without a CDN domain configured (dev, or a deploy before CloudFront) both are
no-ops.
"""

from __future__ import annotations

import re
from functools import lru_cache

from django.conf import settings

# Any regional / legacy / dualstack S3 host spelling for a bucket.
_S3_HOST = r"\.s3(?:[.-](?:dualstack\.)?[a-z0-9-]+)?\.amazonaws\.com"
_SEP = r"(?:/|\\/)"


@lru_cache(maxsize=1)
def _rules() -> tuple[tuple[re.Pattern, str, re.Pattern, str], ...]:
    rules = []
    pairs = (
        (getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""), getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "")),
        (getattr(settings, "AWS_STATIC_BUCKET_NAME", ""), getattr(settings, "AWS_STATIC_CUSTOM_DOMAIN", "")),
    )
    for bucket, cdn in pairs:
        if not (bucket and cdn):
            continue
        b = re.escape(bucket)
        # Virtual-hosted: https://<bucket>.s3.<region>.amazonaws.com/<key>
        vhost = re.compile(rf"https?:({_SEP}{_SEP}){b}{_S3_HOST}(?={_SEP})", re.I)
        # Path-style: https://s3.<region>.amazonaws.com/<bucket>/<key>
        path = re.compile(
            rf"https?:({_SEP}{_SEP})s3(?:[.-](?:dualstack\.)?[a-z0-9-]+)?\.amazonaws\.com({_SEP}){b}(?={_SEP})",
            re.I,
        )
        rules.append((vhost, cdn, path, cdn))
    return tuple(rules)


def enabled() -> bool:
    return bool(_rules())


def rewrite_text(text: str) -> str:
    """Every S3 URL for our buckets in ``text``, re-hosted on the CDN."""
    if not text or "amazonaws.com" not in text:
        return text
    for vhost, cdn, path, _ in _rules():
        text = vhost.sub(lambda m, cdn=cdn: f"https:{m.group(1)}{cdn}", text)
        text = path.sub(lambda m, cdn=cdn: f"https:{m.group(1)}{cdn}", text)
    return text


def cdn_url(url: str) -> str:
    """One URL, re-hosted on the CDN when it points at one of our buckets."""
    try:
        return rewrite_text(url) if url else url
    except Exception:
        return url
