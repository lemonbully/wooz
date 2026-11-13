"""Plan-based feature gating for the store dashboard.

A free-plan owner only gets the dashboard areas the admin ticked on the free
plan (``SubscriptionPlan.included_features``); every other area shows an
"Upgrade" badge in the sidebar and, if opened, an upgrade wall instead of the
page. Owners with an active paid subscription  or when no free plan is
configured to gate  get everything.

The gate is enforced once per request in :mod:`commerce.middleware`; the sidebar
badges and the wall read the same allowed-set, computed here and memoised on the
request so a store page load costs at most one extra query.
"""

from __future__ import annotations

# Every store route name → the dashboard feature it belongs to. The feature keys
# are exactly the sidebar keys in ``commerce.context.NAV_GROUPS``. Anything not
# listed (or mapped to ``None``) is never gated  e.g. the courier webhook, which
# is an external callback, not an owner action.
URLNAME_TO_FEATURE: dict[str, str | None] = {
    # Storefront
    "editor": "editor", "editor_save_section": "editor",
    "editor_preview": "editor", "editor_upload": "editor",
    "media": "media", "media_list": "media", "media_upload": "media",
    "media_update": "media", "media_delete": "media", "media_delete_bulk": "media",
    "domain": "domain", "domain_save": "domain", "domain_verify": "domain",
    "domain_ssl_status": "domain", "domain_remove": "domain",
    # Operate
    "overview": "overview",
    "orders": "orders", "orders_auto_ship": "orders", "order_detail": "orders",
    "order_edit": "orders", "order_status": "orders", "order_tracking_json": "orders",
    "order_sync_status": "orders", "order_serviceability": "orders",
    "orders_rate_quotes": "orders",
    "orders_export": "orders",
    "courier_webhook": None,
    "requests": "requests", "request_resolve": "requests",
    "enquiries": "enquiries", "enquiry_mark_read": "enquiries", "enquiry_delete": "enquiries",
    "customers": "customers", "customer_notify_all": "customers",
    "customer_credit_bulk": "customers", "customer_detail": "customers",
    "customer_json": "customers", "customer_edit": "customers",
    "customer_delete": "customers", "customer_credits_list": "customers",
    "customer_credit_add": "customers", "customer_notify": "customers",
    "customer_credit_delete": "customers", "customer_credit_bulk_list": "customers",
    "customer_credit_bulk_delete": "customers",
    # Catalogue
    "products": "products", "product_new": "products", "product_flag": "products",
    "best_sellers_order": "products", "best_sellers_subtitle": "products",
    "enquiry_form_mode": "products", "product_edit": "products",
    "toggle_best_seller": "products", "product_duplicate": "products",
    "product_delete": "products",
    "trust_badge_create": "products", "trust_badge_delete": "products",
    "inventory": "inventory", "product_restock": "inventory",
    "out_of_stock": "out_of_stock",
    "categories": "categories", "categories_mega_menu": "categories",
    "trusted_icons_toggle": "categories", "trusted_icons_save": "categories",
    "category_new": "categories", "category_highlight": "categories",
    "category_edit": "categories", "category_delete": "categories",
    "reviews": "reviews", "review_moderate": "reviews",
    # Grow
    "analytics": "analytics",
    "email_marketing": "email_marketing",
    "email_analytics_json": "email_marketing", "email_analytics": "email_marketing",
    "email_campaign_segments": "email_marketing", "email_campaign_segment_export": "email_marketing",
    "email_campaign_new": "email_marketing", "email_campaign_edit": "email_marketing",
    "email_campaign_send": "email_marketing", "email_campaign_pause": "email_marketing",
    "email_campaign_resume": "email_marketing", "email_campaign_delete": "email_marketing",
    "email_audiences": "email_marketing", "email_audience_detail": "email_marketing",
    "email_audience_import": "email_marketing", "email_audience_add": "email_marketing",
    "email_audience_from_customers": "email_marketing", "email_audience_delete": "email_marketing",
    "email_audience_member_delete": "email_marketing",
    "email_sequences": "email_marketing", "email_sequence_new": "email_marketing",
    "email_sequence_edit": "email_marketing", "email_sequence_delete": "email_marketing",
    "email_suppression_delete": "email_marketing",
    "email_ai_generate": "email_marketing",
    "email_template_json": "email_marketing", "email_template_preview": "email_marketing",
    "email_design_preview": "email_marketing",
    "coupons": "coupons", "coupon_new": "coupons", "coupon_edit": "coupons",
    "coupon_delete": "coupons",
    "instant_discounts": "coupons", "instant_discount_delete": "coupons",
    "collaborations": "collaborations", "collaboration_resend": "collaborations",
    "collaboration_suspend": "collaborations", "collaboration_earnings": "collaborations",
    "collaboration_payout": "collaborations", "collaboration_contract_url": "collaborations",
    "collaboration_payout_request_resolve": "collaborations",
    "tracking": "tracking",
    "google_merchant": "google_merchant", "google_merchant_sync": "google_merchant",
    "google_merchant_auto_sync": "google_merchant", "google_merchant_disconnect": "google_merchant",
    "google_merchant_connect": "google_merchant", "google_merchant_select_account": "google_merchant",
    "google_merchant_status": "google_merchant", "google_merchant_claim": "google_merchant",
    "google_merchant_refresh_statuses": "google_merchant",
    # Money & delivery
    "payments": "payments",
    "shipping": "shipping", "warehouse_default": "shipping", "warehouse_delete": "shipping",
    "shiprocket_pickup_locations": "shipping",
    # The wallet box is shown on both Orders and Shipping; Orders access is
    # what a dispatcher has.
    "shiprocket_wallet": "orders",
    "bills": "bills", "bill_detail": "bills", "bill_generate": "bills",
    "bill_pdf": "bills", "bill_export": "bills", "bill_payment_submit": "bills",
    # Store
    "settings": "settings",
    # Team management is owner-only  intentionally unmapped (feature None), so a
    # staff member is never granted these routes by the access gate.
}


def feature_for_urlname(url_name: str | None) -> str | None:
    """The dashboard feature a store route belongs to, or ``None`` (never gate)."""
    if not url_name:
        return None
    return URLNAME_TO_FEATURE.get(url_name)


def _gating_free_plan(user):
    """The grant whose limits apply to this owner, or ``None`` = no gating.

    ``None`` (everything allowed) when the owner has an active paid / one-time
    subscription, or when no free plan is configured. Otherwise the object whose
    ``included_*`` sets gate the owner: their frozen
    :class:`~billing.models.AccountEntitlement` when they have settled a
    subscription, or  when they never went through checkout, so no snapshot
    exists  the free ``SubscriptionPlan`` they hold, or the configured default
    free plan. Both expose the same ``included_features`` /
    ``included_editor_tabs`` attributes, so :func:`_gate_set` reads either.

    This is the single place the subscription lookup lives, so the dashboard
    feature gate and the editor-tab gate always agree on *which* plan applies 
    they only differ in *which* set of that plan they read.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    from django.utils import timezone

    from billing import entitlements
    from billing.models import SubscriptionOrder, SubscriptionPlan

    # Prefer the owner's frozen grant: the gate reads the plan terms captured when
    # they last settled, so editing a plan never changes an existing owner. A
    # paid/one-time grant unlocks everything; a free grant is gated by its own
    # frozen ``included_*`` sets  which live on the entitlement under the same
    # attribute names the plan uses, so ``_gate_set`` reads it unchanged. Owners
    # who never settled anything have no snapshot and fall through to the live
    # lookup below.
    ent = entitlements.for_user(user)
    if ent is not None:
        if ent.plan_type != SubscriptionPlan.Kind.FREE:
            return None
        return ent

    email = (getattr(user, "email", "") or "").strip()
    if email:
        # The owner's current subscription, if any. A free-plan grant is settled
        # as a PAID order too (so its period/record match a purchase), so "paid"
        # alone can't mean "unlock everything"  it's the *plan type* that
        # decides. A paid / one-time plan (bought, or granted free via an invite)
        # unlocks everything; a free *plan* is gated by its own included sets.
        active = (
            SubscriptionOrder.objects.filter(
                customer_email__iexact=email,
                status=SubscriptionOrder.Status.PAID,
                current_period_end__gte=timezone.localdate(),
            )
            .select_related("plan")
            .order_by("-current_period_end")
            .first()
        )
        if active is not None:
            plan = active.plan
            if plan is None or plan.plan_type != SubscriptionPlan.Kind.FREE:
                return None  # a real paid/one-time subscription (or legacy) → all
            return plan  # free plan → gated by its own sets

    # No active subscription on record  fall back to the configured free plan,
    # so a signed-up owner who never went through checkout is still gated by it.
    return (
        SubscriptionPlan.objects.filter(
            plan_type=SubscriptionPlan.Kind.FREE, is_active=True
        )
        .order_by("order")
        .first()
    )


def _gate_set(plan, attr: str) -> set[str] | None:
    """The keys a free ``plan`` includes for ``attr`` (``None`` = don't gate).

    An empty / unset list means the plan imposes no limit for that dimension.
    """
    if plan is None:
        return None
    keys = [k for k in (getattr(plan, attr, None) or []) if isinstance(k, str)]
    return set(keys) if keys else None


def allowed_features_for_owner(user) -> set[str] | None:
    """The dashboard feature keys an owner may use, or ``None`` = everything.

    ``None`` (no gating) when the owner has an active paid subscription, when no
    free plan exists, or when the free plan hasn't been configured to gate
    (no features ticked). Otherwise the free plan's ticked set.
    """
    return _gate_set(_gating_free_plan(user), "included_features")


def allowed_editor_tabs_for_owner(user) -> set[str] | None:
    """The content-editor tab keys an owner may edit, or ``None`` = every tab.

    Same resolution as :func:`allowed_features_for_owner` (a paid subscription
    unlocks everything; a free plan gates), but reads the free plan's
    ``included_editor_tabs`` set instead.
    """
    return _gate_set(_gating_free_plan(user), "included_editor_tabs")


_SENTINEL = object()


def allowed_features(request) -> set[str] | None:
    """Request-memoised :func:`allowed_features_for_owner` (``None`` = all)."""
    cached = getattr(request, "_ow_allowed_features", _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    value = allowed_features_for_owner(getattr(request, "user", None))
    try:
        request._ow_allowed_features = value
    except Exception:
        pass
    return value


def is_locked(request, feature: str | None) -> bool:
    """True when ``feature`` is gated for this request's owner."""
    if not feature:
        return False
    allowed = allowed_features(request)
    if allowed is None:
        return False
    return feature not in allowed


def allowed_editor_tabs(request) -> set[str] | None:
    """Request-memoised :func:`allowed_editor_tabs_for_owner` (``None`` = all)."""
    cached = getattr(request, "_ow_allowed_editor_tabs", _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    value = allowed_editor_tabs_for_owner(getattr(request, "user", None))
    try:
        request._ow_allowed_editor_tabs = value
    except Exception:
        pass
    return value


def is_editor_tab_locked(request, tab_key: str | None) -> bool:
    """True when a content-editor tab is gated for this request's owner."""
    if not tab_key:
        return False
    allowed = allowed_editor_tabs(request)
    if allowed is None:
        return False
    return tab_key not in allowed
