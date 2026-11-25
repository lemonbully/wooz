"""Gunicorn access logging for production.

Two changes from Gunicorn's stock logger, both aimed at CloudWatch:

* Health checks are not logged. The ALB probes every task every 15 seconds and
  the container HEALTHCHECK curls it too, which made health checks about a fifth
  of all log lines (ingestion is billed per GB, and they drown real traffic).
* Each line also records the Host header and the request time in milliseconds,
  so traffic and latency can be split per surface (dashboard, marketing,
  storefronts, custom domains). The original fields stay first, in the same
  order, so existing Logs Insights queries keep parsing.

Wired up in deploy/entrypoint.sh via --logger-class and --access-logformat.
"""

from gunicorn.glogging import Logger

# Paths answered by core.health. Matched on the path only (no query string).
_QUIET_PREFIXES = ("/health",)


class QuietHealthLogger(Logger):
    def access(self, resp, req, environ, request_time):
        path = environ.get("PATH_INFO") or ""
        if path.startswith(_QUIET_PREFIXES):
            return
        super().access(resp, req, environ, request_time)
