"""Signal receivers: revoke marketplace sessions on dashboard sign-out, and
recompile / re-cache when staff change the catalogue in the admin."""

from __future__ import annotations

import logging

from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger("onewooz.marketplace")


@receiver(user_logged_out, dispatch_uid="marketplace_revoke_on_logout")
def _revoke_marketplace_sessions(sender, request=None, user=None, **kwargs):
    try:
        from . import auth

        key = getattr(getattr(request, "session", None), "session_key", "") if request is not None else ""
        auth.revoke_for_dashboard_session(key or "")
    except Exception:
        logger.debug("marketplace revoke on logout skipped", exc_info=True)


def _catalogue_changed(app_id, force: bool = False) -> None:
    try:
        from . import api, tasks

        api.invalidate_all()
        tasks.enqueue_recompile_app(app_id, force=force)
    except Exception:
        logger.warning("catalogue change follow-up failed", exc_info=True)


def _connect():
    from .models import App, AppDashboardPlacement, AppSettingField, AppStorefrontPlacement, MarketplaceSettings

    @receiver(post_save, sender=App, dispatch_uid="mkt_app_saved", weak=False)
    def _app_saved(sender, instance, created, update_fields=None, **kwargs):
        # Breaker pauses save with update_fields and refresh the paused set
        # themselves; only a real admin edit needs a recompile.
        if update_fields:
            return
        _catalogue_changed(instance.pk)

    for model in (AppDashboardPlacement, AppStorefrontPlacement, AppSettingField):
        @receiver(post_save, sender=model, dispatch_uid=f"mkt_{model.__name__}_saved", weak=False)
        def _child_saved(sender, instance, **kwargs):
            _catalogue_changed(instance.app_id)

        @receiver(post_delete, sender=model, dispatch_uid=f"mkt_{model.__name__}_deleted", weak=False)
        def _child_deleted(sender, instance, **kwargs):
            # A removed storefront placement must disappear from stores too.
            _catalogue_changed(instance.app_id, force=True)

    @receiver(post_save, sender=MarketplaceSettings, dispatch_uid="mkt_settings_saved", weak=False)
    def _settings_saved(sender, instance, **kwargs):
        from django.core.cache import cache

        cache.delete("mkt:settings")


_connect()
