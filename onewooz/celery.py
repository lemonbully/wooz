"""Celery application for OneWooz.

Reliability settings are chosen so that losing a worker mid-task never loses
the task: messages are acknowledged only after the task completes, a task whose
worker dies is requeued, and each worker holds exactly one unacknowledged
message so a slow task cannot starve its peers.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onewooz.settings.dev")

app = Celery("onewooz")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    return f"request: {self.request!r}"
