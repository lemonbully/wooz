"""Resolve a store's email-marketing plan limits.

The limits are a dedicated block frozen per owner on the billing entitlement
snapshot (see :func:`billing.entitlements.email_marketing_limits`), so editing a
plan never moves an owner already on it. This wraps that lookup so the engine
and the dashboard read limits by *site* without touching billing internals.
"""

from __future__ import annotations


def limits_for_site(site) -> dict:
    """The email-marketing limits that apply to ``site``'s owner.

    Always returns a fully-populated dict (falling back to the defaults) so the
    engine never has to guard for missing keys.
    """
    owner = getattr(site, "owner", None)
    try:
        from billing.entitlements import email_marketing_limits

        return email_marketing_limits(owner)
    except Exception:
        from billing.entitlements import EMAIL_MARKETING_DEFAULTS

        return dict(EMAIL_MARKETING_DEFAULTS)
