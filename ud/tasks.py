"""Background delivery for the Business Hub (see :mod:`ud.hub`)."""

from __future__ import annotations

from celery import shared_task


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=5, retry_kwargs={"max_retries": 3})
def deliver_business_hub_post(self, post_id: str) -> int:
    """Notify (bell) and queue emails for every recipient of one post."""
    from ud import hub

    return hub.deliver(post_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=10, retry_kwargs={"max_retries": 4})
def send_hub_email(self, receipt_id: str) -> bool:
    """Email one owner about one post. Idempotent per delivery."""
    from ud import hub

    return hub.send_email_for(receipt_id)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=10, retry_kwargs={"max_retries": 4})
def email_support_ticket(self, ticket_id: str) -> bool:
    """Email the OneWooz team about one new support ticket."""
    from ud import support

    return support.email_team(ticket_id)
