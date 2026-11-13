"""Cross-replica advisory lock for deploy-time steps.

Every web replica runs the same boot steps (``deploy/entrypoint.sh``). Steps that
write to shared state, the database schema or the shared S3 static bucket, must
not run on two replicas at once. On MySQL a named ``GET_LOCK`` on a dedicated
connection serialises them: one replica does the work, the others wait for it to
finish and then find nothing left to do. Other backends (SQLite in dev) have a
single writer, so the lock is a no-op there.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connections


@contextmanager
def deploy_lock(name: str, timeout: int):
    """Yield ``True`` once ``name`` is held, ``False`` if the wait timed out."""
    conn = connections[DEFAULT_DB_ALIAS]
    if conn.vendor != "mysql":
        yield True
        return

    # A dedicated raw connection holds the lock, so the guarded step's own
    # connection churn can't drop it out from under us.
    lock_conn = conn.get_new_connection(conn.get_connection_params())
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, %s)", (name, timeout))
            row = cur.fetchone()
        yield bool(row and row[0] == 1)
    finally:
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
        except Exception:
            pass
        try:
            lock_conn.close()
        except Exception:
            pass
