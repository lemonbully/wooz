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
    orders,
    overview,
    settings as store_settings,
)

app_name = "store"

urlpatterns = [
    path("", overview.overview, name="overview"),

    # Orders & fulfilment
    path("orders/", orders.orders, name="orders"),
    path("orders/<uuid:order_id>/", orders.order_detail, name="order_detail"),
    path("orders/<uuid:order_id>/status/", orders.update_status, name="order_status"),
    path("requests/", orders.requests, name="requests"),
    path("requests/<uuid:request_id>/resolve/", orders.resolve_request, name="request_resolve"),
    path("enquiries/", orders.enquiries, name="enquiries"),
    path("customers/", customers.customers, name="customers"),
    path("customers/<uuid:customer_id>/", customers.customer_detail, name="customer_detail"),

    # Catalogue
    path("products/", catalog.products, name="products"),
    path("products/new/", catalog.product_form, name="product_new"),
    path("products/<uuid:product_id>/", catalog.product_form, name="product_edit"),
    path("products/<uuid:product_id>/delete/", catalog.product_delete, name="product_delete"),
    path("inventory/", catalog.inventory, name="inventory"),
    path("out-of-stock/", catalog.out_of_stock, name="out_of_stock"),
    path("categories/", catalog.categories, name="categories"),
    path("categories/new/", catalog.category_form, name="category_new"),
    path("categories/<uuid:category_id>/", catalog.category_form, name="category_edit"),
    path("categories/<uuid:category_id>/delete/", catalog.category_delete, name="category_delete"),
    path("reviews/", catalog.reviews, name="reviews"),
    path("reviews/<uuid:review_id>/moderate/", catalog.review_moderate, name="review_moderate"),

    # Grow
    path("analytics/", analytics.analytics, name="analytics"),
    path("discounts/", growth.coupons, name="coupons"),
    path("discounts/new/", growth.coupon_form, name="coupon_new"),
    path("discounts/<uuid:coupon_id>/", growth.coupon_form, name="coupon_edit"),
    path("discounts/<uuid:coupon_id>/delete/", growth.coupon_delete, name="coupon_delete"),
    path("collaborations/", growth.collaborations, name="collaborations"),
    path("collaborations/new/", growth.collaboration_form, name="collaboration_new"),
    path("tracking/", integrations.tracking, name="tracking"),
    path("google-merchant/", integrations.google_merchant, name="google_merchant"),

    # Money & delivery
    path("payments/", integrations.payments, name="payments"),
    path("shipping/", integrations.shipping, name="shipping"),
    path("bills/", billing.bills, name="bills"),

    # Custom domain
    path("domain/", domains.domain, name="domain"),
    path("domain/save/", domains.domain_save, name="domain_save"),
    path("domain/verify/", domains.domain_verify, name="domain_verify"),
    path("domain/remove/", domains.domain_remove, name="domain_remove"),

    # Storefront content editor
    path("editor/", editor.editor, name="editor"),
    path("editor/section/<str:section>/", editor.save_section, name="editor_save_section"),
    path("editor/preview/", editor.preview, name="editor_preview"),
    path("editor/upload/", editor.upload_media, name="editor_upload"),

    # Store settings
    path("settings/", store_settings.settings, name="settings"),
]
