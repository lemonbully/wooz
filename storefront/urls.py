"""Storefront routes, written exactly as a visitor sees them.

The host (or the ``/s/<slug>/`` development prefix) already identifies the store,
so nothing here carries a slug. Names match the reference platform's public URL
names so the ported templates' ``{% url %}`` tags resolve unchanged; they are
intentionally left un-namespaced for the same reason.

Route order: concrete paths precede the ``products/<slug>`` product route so a
reserved path (``products/wa/…``) is never swallowed by it.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="public_website"),

    # Catalogue
    path("shop/", views.all_products, name="public_all_products"),
    # The navigation's search form and legacy links target /products/.
    path("products/", views.all_products, name="public_products_index"),
    path("categories/", views.categories, name="public_categories"),
    path("category/<slug:category_slug>/", views.category_detail, name="public_category_detail"),

    # Reserved product sub-routes (must precede the product-slug route)
    path("products/wa/<str:product_id>/", views.product_whatsapp, name="public_product_whatsapp"),
    path("products/<str:product_id>/review/", views.submit_product_review, name="submit_product_review"),
    path("products/<str:product_id>/review/delete/", views.review_delete, name="public_review_delete"),
    path("products/<slug:product_slug>/", views.product_detail, name="public_product_detail"),

    # Content pages
    path("about/", views.about, name="public_about"),
    path("certificates/", views.certificates, name="public_certificates"),
    path("contact/", views.enquiry, name="public_enquiry"),
    path("blog/", views.blogs, name="public_blogs"),
    path("blog/<slug:blog_slug>/", views.blog_detail, name="public_blog_detail"),
    path("page/<slug:page_slug>/", views.core_page, name="public_core_page"),
    path("page/<slug:page_slug>/<slug:sub_slug>/", views.core_page, name="public_core_subpage"),
    path("page/<slug:page_slug>/<slug:sub_slug>/<slug:child_slug>/", views.core_page, name="public_core_child"),

    # Policies
    path("privacy-policy/", views.privacy_policy, name="privacy_policy_page"),
    path("terms-conditions/", views.terms_conditions, name="public_terms"),
    path("refund-policy/", views.refund_policy, name="public_refund"),
    path("shipping-policy/", views.shipping_policy, name="public_shipping"),
    path("custom-policy/", views.custom_policy, name="public_custom_policy"),

    # JSON APIs the storefront JS calls
    path("api/products/suggest/", views.api_product_suggest, name="public_product_suggest"),
    path("api/product/<str:product_id>/variants/", views.api_product_variants, name="public_product_variants"),
    path("api/pincode/", views.pincode_serviceability, name="public_pincode_serviceability"),

    # Transactional surface (stubbed until the commerce phase)
    path("cart/add/<str:product_id>/", views.cart_add, name="public_cart_add"),
    path("checkout/", views.checkout, name="public_checkout"),
    path("wishlist/add/<str:product_id>/", views.wishlist_add, name="public_wishlist_add"),
    path("wishlist/remove/<str:product_id>/", views.wishlist_remove, name="public_wishlist_remove"),
    path("account/", views.user_account, name="public_user_account"),
    path("account/orders/", views.user_orders, name="public_user_orders"),
    path("account/logout/", views.account_logout, name="public_account_logout"),
    path("orders/<str:order_id>/confirmation/", views.order_confirmation, name="public_order_confirmation"),
    path("newsletter/subscribe/", views.subscribe_newsletter, name="subscribe_newsletter"),

    # Owner-preview no-ops (the ported templates guard these behind is_public_view)
    path("_owner/edit/<uuid:site_id>/", views.owner_noop, name="edit_website"),
    path("_owner/apply/<uuid:site_id>/", views.owner_noop, name="apply_enhanced_template"),
]
