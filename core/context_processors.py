"""Template context shared by every surface."""

from __future__ import annotations

from . import links
from .hosts import Surface


def security(request):
    """Security primitives and cross-surface origins.

    Every inline ``<script>`` in this codebase must carry
    ``nonce="{{ csp_nonce }}"``. Anything that forgets is blocked by the
    browser rather than silently running  which is the point of using a nonce
    instead of ``'unsafe-inline'``.

    The ``*_origin`` values are full origins including scheme and, in
    development, port. Templates must never rebuild them from a bare hostname:
    that is how a link ends up pointing at port 80 locally.
    """
    return {
        "csp_nonce": getattr(request, "csp_nonce", ""),
        "surface": getattr(request, "surface", ""),
        "request_id": getattr(request, "request_id", ""),
        "marketing_origin": links.surface_origin(Surface.MARKETING, request),
        "app_origin": links.surface_origin(Surface.APP, request),
        "admin_origin": links.surface_origin(Surface.ADMIN, request),
    }
