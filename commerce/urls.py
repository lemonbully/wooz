"""Routes for the store-management surface.

Mounted at ``store/<uuid:site_id>/`` inside the app urlconf. Nothing here
writes that prefix; ``reverse('store:orders', args=[site.id])`` reproduces it.
Every route belongs to one store the signed-in owner controls.
"""

from __future__ import annotations

from django.urls import path

from .views import (
    analytics,
    billing,
    catalog,
    customers,
    domains,
    editor,
    growth,
    integrations,
    media,
    orders,
    outreach,
    overview,
    settings as store_settings,
    team,
)

app_name = "store"

urlpatterns = [
    path("", overview.overview, name="overview"),

    # Orders & fulfilment
    path("orders/", orders.orders, name="orders"),
    path("orders/auto-ship/", orders.toggle_auto_ship, name="orders_auto_ship"),
    path("orders/export/", orders.orders_export, name="orders_export"),
    path("orders/<uuid:order_id>/", orders.order_detail, name="order_detail"),
    path("orders/<uuid:order_id>/edit/", orders.order_edit, name="order_edit"),
    path("orders/<uuid:order_id>/status/", orders.update_status, name="order_status"),
    path("orders/<uuid:order_id>/tracking/", orders.order_tracking_json, name="order_tracking_json"),
    path("orders/<uuid:order_id>/sync-status/", orders.sync_shipment_status, name="order_sync_status"),
    path("orders/<uuid:order_id>/serviceability/", orders.shiprocket_serviceability, name="order_serviceability"),
    path("orders/rate-quotes/", orders.shiprocket_rate_quotes, name="orders_rate_quotes"),
    path(
        "webhooks/courier/<str:provider>/<str:token>/",
        orders.courier_webhook,
        name="courier_webhook",
    ),
    path("requests/", orders.requests, name="requests"),
    path("requests/<uuid:request_id>/resolve/", orders.resolve_request, name="request_resolve"),
    path("enquiries/", orders.enquiries, name="enquiries"),
    path("enquiries/<uuid:enquiry_id>/read/", orders.enquiry_mark_read, name="enquiry_mark_read"),
    path("enquiries/<uuid:enquiry_id>/delete/", orders.enquiry_delete, name="enquiry_delete"),
    path("customers/", customers.customers, name="customers"),
    path("subscribers/", customers.subscribers, name="subscribers"),
    path("subscribers/export/", customers.subscribers_export, name="subscribers_export"),
    path("subscribers/<uuid:subscriber_id>/delete/", customers.subscriber_delete, name="subscriber_delete"),
    path("customers/notify-all/", customers.customers_notify_all, name="customer_notify_all"),
    path("customers/credit-bulk/", customers.customers_credit_bulk, name="customer_credit_bulk"),
    path("customers/credit-bulk/list/", customers.customers_credit_bulk_list, name="customer_credit_bulk_list"),
    path("customers/credit-bulk/delete/", customers.customers_credit_bulk_delete, name="customer_credit_bulk_delete"),
    path("customers/<uuid:customer_id>/", customers.customer_detail, name="customer_detail"),
    path("customers/<uuid:customer_id>/json/", customers.customer_json, name="customer_json"),
    path("customers/<uuid:customer_id>/edit/", customers.customer_edit, name="customer_edit"),
    path("customers/<uuid:customer_id>/delete/", customers.customer_delete, name="customer_delete"),
    path("customers/<uuid:customer_id>/credits/", customers.customer_credits_list, name="customer_credits_list"),
    path("customers/<uuid:customer_id>/credits/add/", customers.customer_credit_add, name="customer_credit_add"),
    path("customers/<uuid:customer_id>/credits/<uuid:credit_id>/delete/", customers.customer_credit_delete, name="customer_credit_delete"),
    path("customers/<uuid:customer_id>/notify/", customers.customer_notify, name="customer_notify"),

    # Catalogue
    path("products/", catalog.products, name="products"),
    path("products/new/", catalog.product_form, name="product_new"),
    path("products/flag/<str:flag>/", catalog.toggle_product_flag, name="product_flag"),
    path("products/best-sellers/order/", catalog.update_best_sellers_order, name="best_sellers_order"),
    path("products/best-sellers/subtitle/", catalog.update_best_sellers_subtitle, name="best_sellers_subtitle"),
    path("products/enquiry-mode/", catalog.update_enquiry_form_mode, name="enquiry_form_mode"),
    path("products/trust-badges/create/", catalog.trust_badge_create, name="trust_badge_create"),
    path("products/trust-badges/delete/", catalog.trust_badge_delete, name="trust_badge_delete"),
    path("products/<uuid:product_id>/", catalog.product_form, name="product_edit"),
    path("products/<uuid:product_id>/best-seller/", catalog.toggle_best_seller, name="toggle_best_seller"),
    path("products/<uuid:product_id>/duplicate/", catalog.product_duplicate, name="product_duplicate"),
    path("products/<uuid:product_id>/delete/", catalog.product_delete, name="product_delete"),
    path("inventory/", catalog.inventory, name="inventory"),
    path("inventory/<uuid:product_id>/restock/", catalog.product_restock, name="product_restock"),
    path("out-of-stock/", catalog.out_of_stock, name="out_of_stock"),
    path("categories/", catalog.categories, name="categories"),
    path("categories/mega-menu/", catalog.toggle_categories_mega_menu, name="categories_mega_menu"),
    path("categories/trusted-icons/", catalog.toggle_trusted_icons, name="trusted_icons_toggle"),
    path("categories/trusted-icons/save/", catalog.save_trusted_icons, name="trusted_icons_save"),
    path("categories/new/", catalog.category_form, name="category_new"),
    path("categories/<uuid:category_id>/highlight/", catalog.category_set_highlight, name="category_highlight"),
    path("categories/<uuid:category_id>/", catalog.category_form, name="category_edit"),
    path("categories/<uuid:category_id>/delete/", catalog.category_delete, name="category_delete"),
    path("reviews/", catalog.reviews, name="reviews"),
    path("reviews/<uuid:review_id>/moderate/", catalog.review_moderate, name="review_moderate"),

    # Grow
    path("analytics/", analytics.analytics, name="analytics"),

    # Email marketing
    path("email/", outreach.email_marketing, name="email_marketing"),
    path("email/analytics.json", outreach.email_analytics_json, name="email_analytics_json"),
    path("email/analytics/", outreach.email_analytics, name="email_analytics"),
    path("email/campaigns/new/", outreach.email_campaign_new, name="email_campaign_new"),
    path("email/campaigns/<uuid:campaign_id>/", outreach.email_campaign_edit, name="email_campaign_edit"),
    path("email/campaigns/<uuid:campaign_id>/send/", outreach.email_campaign_send, name="email_campaign_send"),
    path("email/campaigns/<uuid:campaign_id>/segments/", outreach.email_campaign_segments, name="email_campaign_segments"),
    path("email/campaigns/<uuid:campaign_id>/segments/export/", outreach.email_campaign_segment_export, name="email_campaign_segment_export"),
    path("email/campaigns/<uuid:campaign_id>/pause/", outreach.email_campaign_pause, name="email_campaign_pause"),
    path("email/campaigns/<uuid:campaign_id>/resume/", outreach.email_campaign_resume, name="email_campaign_resume"),
    path("email/campaigns/<uuid:campaign_id>/delete/", outreach.email_campaign_delete, name="email_campaign_delete"),
    path("email/audiences/", outreach.email_audiences, name="email_audiences"),
    path("email/audiences/from-customers/", outreach.email_audience_from_customers, name="email_audience_from_customers"),
    path("email/audiences/<uuid:list_id>/", outreach.email_audience_detail, name="email_audience_detail"),
    path("email/audiences/<uuid:list_id>/add/", outreach.email_audience_add, name="email_audience_add"),
    path("email/audiences/<uuid:list_id>/import/", outreach.email_audience_import, name="email_audience_import"),
    path("email/audiences/<uuid:list_id>/delete/", outreach.email_audience_delete, name="email_audience_delete"),
    path("email/audiences/<uuid:list_id>/members/<uuid:member_id>/delete/", outreach.email_audience_member_delete, name="email_audience_member_delete"),
    path("email/suppressions/<uuid:suppression_id>/delete/", outreach.email_suppression_delete, name="email_suppression_delete"),
    path("email/sequences/", outreach.email_sequences, name="email_sequences"),
    path("email/sequences/new/", outreach.email_sequence_new, name="email_sequence_new"),
    path("email/sequences/<uuid:sequence_id>/", outreach.email_sequence_edit, name="email_sequence_edit"),
    path("email/sequences/<uuid:sequence_id>/delete/", outreach.email_sequence_delete, name="email_sequence_delete"),
    path("email/ai/generate/", outreach.email_ai_generate, name="email_ai_generate"),
    path("email/templates/<uuid:template_id>.json", outreach.email_template_json, name="email_template_json"),
    path("email/templates/<uuid:template_id>/preview/", outreach.email_template_preview, name="email_template_preview"),
    path("email/designs/promo/preview/", outreach.email_design_preview, name="email_design_preview"),

    path("discounts/", growth.coupons, name="coupons"),
    path("discounts/new/", growth.coupon_form, name="coupon_new"),
    path("discounts/instant/", growth.instant_discounts_page, name="instant_discounts"),
    path("discounts/instant/<uuid:coupon_id>/delete/", growth.instant_discount_delete, name="instant_discount_delete"),
    path("discounts/<uuid:coupon_id>/", growth.coupon_form, name="coupon_edit"),
    path("discounts/<uuid:coupon_id>/delete/", growth.coupon_delete, name="coupon_delete"),
    path("collaborations/", growth.collaborations, name="collaborations"),
    path("collaborations/<uuid:collab_id>/resend/", growth.collaboration_resend, name="collaboration_resend"),
    path("collaborations/<uuid:collab_id>/suspend/", growth.collaboration_suspend, name="collaboration_suspend"),
    path("collaborations/<uuid:collab_id>/earnings/", growth.collaboration_earnings, name="collaboration_earnings"),
    path("collaborations/<uuid:collab_id>/payout/", growth.collaboration_payout, name="collaboration_payout"),
    path(
        "collaborations/payout-requests/<uuid:request_id>/resolve/",
        growth.collaboration_payout_request_resolve,
        name="collaboration_payout_request_resolve",
    ),
    path("collaborations/<uuid:collab_id>/contract-url/", growth.collaboration_contract_url, name="collaboration_contract_url"),
    path("tracking/", integrations.tracking, name="tracking"),
    path("google-merchant/", integrations.google_merchant, name="google_merchant"),
    path("google-merchant/connect/", integrations.google_merchant_connect, name="google_merchant_connect"),
    path("google-merchant/account/", integrations.google_merchant_select_account, name="google_merchant_select_account"),
    path("google-merchant/register-platform/", integrations.google_merchant_register_platform, name="google_merchant_register_platform"),
    path("google-merchant/status/", integrations.google_merchant_status, name="google_merchant_status"),
    path("google-merchant/claim/", integrations.google_merchant_claim, name="google_merchant_claim"),
    path("google-merchant/statuses/", integrations.google_merchant_refresh_statuses, name="google_merchant_refresh_statuses"),
    path("google-merchant/sync/", integrations.google_merchant_sync, name="google_merchant_sync"),
    path("google-merchant/auto-sync/", integrations.google_merchant_auto_sync, name="google_merchant_auto_sync"),
    path("google-merchant/disconnect/", integrations.google_merchant_disconnect, name="google_merchant_disconnect"),

    # Money & delivery
    path("payments/", integrations.payments, name="payments"),
    path("shipping/", integrations.shipping, name="shipping"),
    path("shipping/shiprocket/wallet/", integrations.shiprocket_wallet, name="shiprocket_wallet"),
    path(
        "shipping/shiprocket/pickup-locations/",
        integrations.shiprocket_pickup_locations,
        name="shiprocket_pickup_locations",
    ),
    path("shipping/warehouses/<uuid:warehouse_id>/default/", integrations.warehouse_set_default, name="warehouse_default"),
    path("shipping/warehouses/<uuid:warehouse_id>/delete/", integrations.warehouse_delete, name="warehouse_delete"),
    path("bills/", billing.bills, name="bills"),
    path("bills/generate/", billing.bill_generate, name="bill_generate"),
    path("bills/<uuid:bill_id>/", billing.bill_detail, name="bill_detail"),
    path("bills/<uuid:bill_id>/pdf/", billing.bill_pdf, name="bill_pdf"),
    path("bills/<uuid:bill_id>/export/", billing.bill_export, name="bill_export"),
    path("bills/<uuid:bill_id>/pay/", billing.bill_payment_submit, name="bill_payment_submit"),

    # Custom domain
    path("domain/", domains.domain, name="domain"),
    path("domain/save/", domains.domain_save, name="domain_save"),
    path("domain/verify/", domains.domain_verify, name="domain_verify"),
    path("domain/ssl-status/", domains.domain_ssl_status, name="domain_ssl_status"),
    path("domain/remove/", domains.domain_remove, name="domain_remove"),

    # Media library
    path("media/", media.library, name="media"),
    path("media/list/", media.media_list, name="media_list"),
    path("media/upload/", media.media_upload, name="media_upload"),
    path("media/<uuid:media_id>/update/", media.media_update, name="media_update"),
    path("media/<uuid:media_id>/delete/", media.media_delete, name="media_delete"),
    path("media/delete-bulk/", media.media_delete_bulk, name="media_delete_bulk"),

    # Storefront content editor
    path("editor/", editor.editor, name="editor"),
    path("editor/section/<str:section>/", editor.save_section, name="editor_save_section"),
    path("editor/preview/", editor.preview, name="editor_preview"),
    path("editor/map-preview/", editor.map_preview, name="editor_map_preview"),
    path("editor/upload/", editor.upload_media, name="editor_upload"),

    # Store settings
    path("settings/", store_settings.settings, name="settings"),

    # Staff & admins (owner-only team management)
    path("staff/", team.staff, name="staff"),
    path("staff/<uuid:member_id>/", team.staff_edit, name="staff_edit"),
    path("staff/<uuid:member_id>/remove/", team.staff_remove, name="staff_remove"),
]
