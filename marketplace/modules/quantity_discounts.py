"""The ``quantity_discounts`` module: what makes the Quantity Discounts app work.

The rules and the pricing live in ``commerce`` (``commerce.models.quantity``,
``commerce.quantity_pricing``) because storefronts may not import the
marketplace. This module only:

* switches the store-side flag ``QuantityDiscountSettings.app_active`` for
  every store of an owner when the app is installed, uninstalled, or its
  licence is granted / expires / is revoked (decided on write, never on a
  shopper's request; rules are kept, so a reinstall brings them back);
* provides the dashboard page (``rules``), rendered by
  :mod:`commerce.views.quantity_discounts` inside the marketplace app boundary.

The product-page block is a storefront *live block* registered by commerce
(:mod:`commerce.storefront_blocks`) and rendered in the ``pdp.after_variations``
slot of every theme.
"""

from __future__ import annotations

import logging

from . import Module

logger = logging.getLogger("onewooz.marketplace")

SLUG = "quantity-discounts"


def _active_for(owner, app) -> bool:
    from .. import licences

    try:
        return bool(licences.is_installed(owner, app) and licences.has_app(owner, app))
    except Exception:
        logger.warning("quantity discounts: licence check failed", exc_info=True)
        return False


def sync_site(site, app) -> bool:
    """Bring one store's flag in line with the owner's install + licence."""
    from commerce.models import QuantityDiscountSettings

    from ..licences import owner_of

    active = _active_for(owner_of(site.owner), app)
    row = QuantityDiscountSettings.objects.for_site(site).first()
    if row is None:
        QuantityDiscountSettings.objects.create(site=site, app_active=active)
    elif row.app_active != active:
        row.app_active = active
        row.save(update_fields=["app_active", "updated_at"])
    return active


def sync_owner(owner_ref, app) -> None:
    """Every store of this owner (not deleted)."""
    from tenancy.models import Site

    for site in Site.objects.filter(owner_id=owner_ref, is_deleted=False).defer("content").select_related("owner"):
        try:
            sync_site(site, app)
        except Exception:
            logger.warning("quantity discounts: sync failed for site %s", site.pk, exc_info=True)


class QuantityDiscounts(Module):
    key = "quantity_discounts"
    name = "Quantity discounts (tiered unit prices)"
    description = "Lower the unit price as shoppers buy more; limits and bulk-enquiry above the last tier."

    def pages(self) -> dict:
        return {"rules": self._rules_page}

    @staticmethod
    def _rules_page(request, site, app, placement):
        sync_site(site, app)
        from commerce.views.quantity_discounts import manage

        return manage(request, site, app=app, placement=placement)

    def on_install(self, *, owner_ref, owner_email, app) -> None:
        sync_owner(owner_ref, app)

    def on_uninstall(self, *, owner_ref, owner_email, app) -> None:
        sync_owner(owner_ref, app)

    def on_licence_change(self, *, licence, event: str) -> None:
        sync_owner(licence.owner_ref, licence.app)
