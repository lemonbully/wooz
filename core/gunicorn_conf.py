"""Gunicorn hooks for production (loaded with --config python:core.gunicorn_conf).

Warm-up: Gunicorn recycles each worker process after --max-requests, and a
fresh process does its one-time work (importing view modules, building the
URL resolver, compiling templates, opening the database connection) on the
first request it serves. Measured on the marketplace service, that first visitor
waited about 4 seconds while everyone after them got about 0.13 seconds.

``post_worker_init`` runs after the application is loaded and BEFORE the worker
starts accepting connections (gunicorn/workers/base.py: load_wsgi ->
post_worker_init -> run). Calling the WSGI app here for a few read-only pages
moves that one-time cost off real visitors; the other workers keep serving
meanwhile.

GUNICORN_WARMUP lists the pages as comma-separated ``host/path`` entries, set
per role by deploy/entrypoint.sh. Empty means no warm-up. The warm-up is
bounded (WARMUP_TIMEOUT seconds in total) and can never stop a worker from
starting: any error is logged and ignored.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time

WARMUP_TIMEOUT = float(os.environ.get("GUNICORN_WARMUP_TIMEOUT", "25"))


def _targets() -> list[tuple[str, str]]:
    out = []
    for raw in (os.environ.get("GUNICORN_WARMUP") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        host, _, path = raw.partition("/")
        out.append((host, "/" + path))
    return out


def _environ(host: str, path: str) -> dict:
    path, _, query = path.partition("?")
    return {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": host,
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": host,
        "HTTP_X_FORWARDED_PROTO": "https",
        "HTTP_USER_AGENT": "onewooz-warmup/1.0",
        "HTTP_ACCEPT": "text/html",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }


def _warm(worker) -> None:
    app = worker.wsgi
    for host, path in _targets():
        status = {}

        def start_response(s, headers, exc_info=None, _status=status):
            _status["s"] = s
            return lambda _body: None

        started = time.monotonic()
        try:
            result = app(_environ(host, path), start_response)
            try:
                for _chunk in result:
                    pass
            finally:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
            worker.log.info(
                "warm-up %s%s -> %s in %.2fs", host, path, status.get("s", "?"), time.monotonic() - started
            )
        except Exception as exc:  # noqa: BLE001 - warm-up must never break a worker
            worker.log.warning("warm-up %s%s failed: %s", host, path, exc)
    try:
        from django.db import connections

        connections.close_all()
    except Exception:  # noqa: BLE001
        pass


def post_worker_init(worker):
    if not _targets():
        return
    thread = threading.Thread(target=_warm, args=(worker,), name="warmup", daemon=True)
    thread.start()
    thread.join(timeout=WARMUP_TIMEOUT)
    if thread.is_alive():
        worker.log.warning("warm-up still running after %.0fs; serving anyway", WARMUP_TIMEOUT)
