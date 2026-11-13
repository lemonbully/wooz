"""Commerce fee: order-count Commission tiers become price ranges.

The admin used to ask for a commerce fee per *order-count* range (minimum orders
to maximum orders). It now asks for a *price* range (minimum value to maximum
value): a product sold at a price inside the range is charged that range's fee.
The numbers already typed keep their meaning as the two ends of the range, so a
tier "0 to 299 → ₹9" becomes the price range "₹0 – ₹299 → ₹9".

* A plan with tiers and no price ranges gets one price range per tier, and its
  tiers are removed (the tiers table is no longer shown in the admin).
  A plan that already had price ranges was charged by those (they always took
  precedence), so it is left exactly as it is.
* A frozen owner entitlement that was charged by its frozen tiers gets the same
  ranges frozen as price ranges, so a later plan edit still never moves them.

Data only, no schema change. Reverse is a no-op.
"""

from decimal import Decimal

from django.db import migrations


def _dec(value):
    return Decimal(str(value if value not in (None, "") else 0))


def forwards(apps, schema_editor):
    Tier = apps.get_model("billing", "CommissionTier")
    Band = apps.get_model("billing", "CommerceFeeBand")
    Entitlement = apps.get_model("billing", "AccountEntitlement")

    # Plans charged by price ranges before this migration (ranges took precedence).
    had_bands = set(Band.objects.values_list("plan_id", flat=True).distinct())

    tier_plans = set(Tier.objects.values_list("plan_id", flat=True).distinct())
    for plan_id in tier_plans - had_bands:
        tiers = list(Tier.objects.filter(plan_id=plan_id).order_by("order", "min_orders"))
        for t in tiers:
            Band.objects.create(
                plan_id=plan_id,
                min_price=_dec(t.min_orders),
                max_price=(_dec(t.max_orders) if t.max_orders is not None else None),
                fee=_dec(t.per_order_fee),
                order=t.order or 0,
            )
        Tier.objects.filter(plan_id=plan_id).delete()

    for ent in Entitlement.objects.all().iterator():
        if ent.commerce_fee_bands or not ent.commission:
            continue
        if ent.source_plan_id and ent.source_plan_id in had_bands:
            continue  # was charged by its plan's price ranges, unchanged
        bands = []
        for t in ent.commission:
            if not isinstance(t, dict):
                continue
            try:
                bands.append({
                    "min_price": str(_dec(t.get("min_orders")).quantize(Decimal("0.01"))),
                    "max_price": (
                        str(_dec(t["max_orders"]).quantize(Decimal("0.01")))
                        if t.get("max_orders") not in (None, "") else None
                    ),
                    "fee": str(_dec(t.get("per_order_fee")).quantize(Decimal("0.01"))),
                })
            except Exception:
                continue
        if bands:
            ent.commerce_fee_bands = bands
            ent.save(update_fields=["commerce_fee_bands"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0021_subscriptionplan_hide_on_dashboard_upgrade"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
