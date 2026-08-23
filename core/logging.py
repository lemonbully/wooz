"""Logging helpers.

Attaches the per-request correlation id to every log record so a line in the
application log, an audit row, and an error in Sentry can be tied together.
"""

from __future__ import annotations

import contextvars
import logging

#: Set by RequestIDMiddleware at the start of each request.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "onewooz_request_id", default="-"
)


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
