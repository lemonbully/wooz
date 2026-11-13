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
from django.views.generic import RedirectView

from . import hosted_pay, views

urlpatterns = [
    path("", views.home, name="public_website"),

    # Crawler files. Both live at the store root and carry a dot, so the greedy
    # single-segment core-page route (a slug, which excludes dots) never matches
    # them  but they are declared up here for clarity all the same.
    path("robots.txt", views.robots_txt, name="public_robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="public_sitemap_xml"),
    # The browser requests /favicon.ico for any response without an HTML head
    # (robots.txt, sitemap.xml, redirects), where <link rel="icon"> can't reach.
    # Serve the owner's favicon here so it renders on every page and every host.
    path("favicon.ico", views.favicon, name="public_favicon"),

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
    # The blog lives at /blogs/ (what the nav/footer link to). The older /blog/
    # paths 301 there, so each post has exactly one URL for canonical, sitemap,
    # breadcrumbs and schema alike, instead of two self-canonical duplicates.
    path("blogs/", views.blogs, name="public_blogs"),
    path("blogs/<slug:blog_slug>/", views.blog_detail, name="public_blog_detail"),
    path("blog/", RedirectView.as_view(pattern_name="public_blogs", permanent=True, query_string=True), name="public_blogs_alias"),
    path("blog/<slug:blog_slug>/", RedirectView.as_view(pattern_name="public_blog_detail", permanent=True, query_string=True), name="public_blog_detail_alias"),

    # Reference-path aliases. The ported navigation and footer link to the
    # 1matrix literal paths (about-us, enquiry) rather than the canonical
    # about/contact, so serve those paths too  otherwise those menu links
    # 404 and the pages look "missing".
    path("about-us/", views.about, name="public_about_us"),
    path("enquiry/", views.enquiry, name="public_enquiry_alias"),

    # Core pages index. The ported nav and footer link to /core-pages/ and each
    # page lives at the bare slug (/<slug>/…), so this must precede the catch-all
    # single-segment core-page routes appended at the very end of this list.
    path("core-pages/", views.core_pages_index, name="public_core_pages"),

    # Policies
    path("privacy-policy/", views.privacy_policy, name="privacy_policy_page"),
    path("terms-conditions/", views.terms_conditions, name="public_terms"),
    path("refund-policy/", views.refund_policy, name="public_refund"),
    path("shipping-policy/", views.shipping_policy, name="public_shipping"),
    path("custom-policy/", views.custom_policy, name="public_custom_policy"),
    # The owner's own extra policies (Edit Content → Policies, up to three).
    path("policies/<slug:policy_slug>/", views.custom_policy_page, name="public_policy_page"),

    # JSON APIs the storefront JS calls
    path("api/track/", views.track, name="public_track"),
    path("api/products/suggest/", views.api_product_suggest, name="public_product_suggest"),
    path("api/product/<str:product_id>/variants/", views.api_product_variants, name="public_product_variants"),
    path("api/product/<str:product_id>/price-history/", views.api_product_price_history, name="public_product_price_history"),
    path("api/pincode/", views.pincode_serviceability, name="public_pincode_serviceability"),

    # Transactional surface (stubbed until the commerce phase)
    path("cart/", views.cart_view, name="public_cart"),
    path("cart/data/", views.cart_data, name="public_cart_data"),
    path("cart/add/<str:product_id>/", views.cart_add, name="public_cart_add"),
    # Drawer update (product id + action in the body); the /<id>/ form is the page.
    path("cart/update/", views.cart_update_body, name="public_cart_update_body"),
    path("cart/update/<str:product_id>/", views.cart_update, name="public_cart_update"),
    path("cart/remove/<str:product_id>/", views.cart_remove, name="public_cart_remove"),
    path("cart/coupon/", views.cart_apply_coupon, name="public_cart_coupon"),
    path("checkout/", views.checkout, name="public_checkout"),
    path("checkout/place/", views.checkout_place, name="public_checkout_place"),
    path("checkout/verify/", views.payment_verify, name="public_payment_verify"),
    # A UGC store's Cashfree payment, run on this (the owner's whitelisted)
    # domain: signed short-lived link → Cashfree → return here → back to UGC.
    path("pay/<str:token>/", hosted_pay.pay, name="public_hosted_pay"),
    path("pay/<str:token>/done/", hosted_pay.done, name="public_hosted_pay_done"),
    path("webhooks/razorpay/", views.webhook_razorpay, name="public_webhook_razorpay"),
    path("webhooks/cashfree/", views.webhook_cashfree, name="public_webhook_cashfree"),
    # Inbound courier tracking (Shiprocket parity). Keyword-free path so the
    # Shiprocket dashboard accepts the URL; token-in-path variant for portals
    # that reject query-string tokens.
    path("webhooks/tracking/", views.webhook_courier, name="public_webhook_courier"),
    path("webhooks/tracking/<str:token>/", views.webhook_courier_token, name="public_webhook_courier_token"),
    path("wishlist/add/<str:product_id>/", views.wishlist_add, name="public_wishlist_add"),
    path("wishlist/remove/<str:product_id>/", views.wishlist_remove, name="public_wishlist_remove"),
    path("account/", views.user_account, name="public_user_account"),
    path("account/orders/", views.user_orders, name="public_user_orders"),
    path("account/send-otp/", views.account_send_otp, name="public_account_send_otp"),
    path("account/verify-otp/", views.account_verify_otp, name="public_account_verify_otp"),
    path("account/logout/", views.account_logout, name="public_account_logout"),
    path("account/profile/", views.account_profile_save, name="public_account_profile"),
    path("account/email/send-otp/", views.account_email_send_otp, name="public_account_email_otp"),
    path("account/email/verify/", views.account_email_verify, name="public_account_email_verify"),
    path("account/addresses/save/", views.account_address_save, name="public_account_address_save"),
    path("account/addresses/<uuid:address_id>/delete/", views.account_address_delete, name="public_account_address_delete"),
    path("account/delete/send-otp/", views.account_delete_send_otp, name="public_account_delete_otp"),
    path("account/delete/confirm/", views.account_delete_confirm, name="public_account_delete_confirm"),
    path("orders/<str:order_id>/confirmation/", views.order_confirmation, name="public_order_confirmation"),
    path("account/orders/<str:order_id>/invoice/", views.order_invoice, name="public_order_invoice"),
    path("account/orders/<str:order_id>/request/", views.account_order_request, name="public_order_request"),
    path("newsletter/subscribe/", views.subscribe_newsletter, name="subscribe_newsletter"),

    # Owner-preview no-ops (the ported templates guard these behind is_public_view)
    path("_owner/edit/<uuid:site_id>/", views.owner_noop, name="edit_website"),
    path("_owner/apply/<uuid:site_id>/", views.owner_noop, name="apply_enhanced_template"),

    # Core pages live at the bare slug, mirroring the reference and the ported
    # designs' links (nav, footer, breadcrumb, the in-page SPA that pushes
    # /<slug>/<sub>/<child>/). These are greedy single-/two-/three-segment
    # patterns, so they MUST stay last  every reserved route above is matched
    # first, and only an otherwise-unmatched path falls through to a core page.
    path("<slug:page_slug>/", views.core_page, name="public_core_page"),
    path("<slug:page_slug>/<slug:sub_slug>/", views.core_page, name="public_core_subpage"),
    path("<slug:page_slug>/<slug:sub_slug>/<slug:child_slug>/", views.core_page, name="public_core_child"),
]
