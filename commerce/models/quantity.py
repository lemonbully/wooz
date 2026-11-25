"""Quantity discounts (the "Quantity Discounts" marketplace app).

Amazon-style tiered pricing: "buy 2 or more, save 5%; buy 5 or more, save
10%". There is no extra block on the product page: when the shopper changes
the quantity, the design's own "price per piece" changes in place. The tier
lowers the line's *unit price*, so the cart, GST, coupons, invoices, Shiprocket
and the commerce fee all see the price the shopper actually pays.

The rules live here, in ``commerce``, and never in ``marketplace``: storefronts
may not import the marketplace (check ``marketplace.E001``). The marketplace
app only flips :attr:`QuantityDiscountSettings.app_active` when it is
installed, uninstalled or its licence changes (decided on write, never on a
shopper's request). The pricing maths is :mod:`commerce.quantity_pricing`.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.models import SoftDeleteMixin, TenantScopedModel


class QuantityDiscountSettings(TenantScopedModel):
    """One row per store: is the app live here, and store-wide switches."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="quantity_discount_settings"
    )
    #: Written only by the marketplace module (install / uninstall / licence).
    #: Rules are kept when it goes False and apply again on reinstall.
    app_active = models.BooleanField(default=False)
    #: Whether discount codes / automatic discounts also apply to lines that
    #: already carry a quantity price.
    combine_with_coupons = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("site",), name="uniq_qty_discount_settings_site"),
        ]

    def __str__(self) -> str:
        return f"Quantity discount settings ({'on' if self.app_active else 'off'})"


class QuantityDiscountRule(TenantScopedModel, SoftDeleteMixin):
    """A set of quantity tiers for the whole store, some categories or some
    products. The most specific rule wins: product, then category, then store.
    """

    class Scope(models.TextChoices):
        STORE = "store", "Every product"
        CATEGORIES = "categories", "Specific categories"
        PRODUCTS = "products", "Specific products"

    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percentage off each unit"
        AMOUNT_OFF = "amount_off", "Amount off each unit"
        FIXED_PRICE = "fixed_price", "Fixed price per unit"

    class Beyond(models.TextChoices):
        SAME = "same", "Keep the last tier's discount for any larger quantity"
        WHATSAPP = "whatsapp", "Above a limit, talk on WhatsApp for a custom price"
        LIMIT = "limit", "Don't sell more than a limit per order"
        CUSTOM = "custom", "Above a limit, show my own message"

    class ContactAction(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        ENQUIRY = "enquiry", "Enquiry form"
        CALL = "call", "Phone call"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="quantity_discount_rules"
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.PRODUCTS)
    # Id lists, like Coupon: re-resolved through ``.for_site`` when used, so a
    # stale id is ignored and can never reach another store.
    product_ids = models.JSONField(default=list, blank=True)
    category_ids = models.JSONField(default=list, blank=True)

    discount_type = models.CharField(
        max_length=12, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    #: ``[{"min_qty": 2, "value": "5.00"}, ...]`` ascending by min_qty (1 to 5 tiers).
    tiers = models.JSONField(default=list, blank=True)
    #: Count every variation of a product together (1 red + 1 blue = 2 units).
    count_variations_together = models.BooleanField(default=True)

    #: What happens above the last tier. Required: the owner always decides.
    beyond = models.CharField(max_length=10, choices=Beyond.choices, default=Beyond.SAME)
    #: The limit N for WHATSAPP / LIMIT / CUSTOM (at least the last tier's quantity, at most 100).
    beyond_qty = models.PositiveIntegerField(null=True, blank=True)
    custom_message = models.CharField(max_length=240, blank=True, default="")
    #: Unused since the product page shows no block (kept so stored rules and
    #: the existing migration stay valid).
    custom_action = models.CharField(
        max_length=10, choices=ContactAction.choices, default=ContactAction.WHATSAPP
    )

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("site", "is_active", "is_deleted"))]

    def __str__(self) -> str:
        return self.name or "Quantity discount"

    # -- helpers -----------------------------------------------------------
    def tier_list(self) -> list[dict]:
        """Tiers cleaned and sorted: ``[{"min_qty": int, "value": Decimal}]``."""
        out = []
        for t in self.tiers or []:
            if not isinstance(t, dict):
                continue
            try:
                q = int(t.get("min_qty"))
                v = Decimal(str(t.get("value")))
            except Exception:
                continue
            if q >= 1 and v > 0:
                out.append({"min_qty": q, "value": v})
        out.sort(key=lambda t: t["min_qty"])
        return out

    @property
    def has_limit(self) -> bool:
        return self.beyond != self.Beyond.SAME and bool(self.beyond_qty)
