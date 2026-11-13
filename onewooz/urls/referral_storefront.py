"""URLs for a UGC *collaborator* storefront (``<sub>.myugcstore.com``).

Mirrors ``onewooz.urls.storefront`` path-for-path and name-for-name, so every
link, form action and ``{% url %}`` on a referral host resolves to a referral
page  a visitor can never be routed onto the owner's themed website. Page
renders go to :mod:`storefront.referral` (the card-shell views); the cart,
checkout, account, order-confirmation and every JSON/action endpoint reuse the
existing :mod:`storefront.views` (they are theme-agnostic, and the shared
checkout/cart/account templates re-skin into the referral chrome via the
``store_base`` set in ``storefront.context.build_context``).
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from django.views.generic import RedirectView

from core import health
from storefront import referral as ref
from storefront import views as sf

urlpatterns = [
    path("health/live/", health.live, name="health_live"),

    path("", ref.home, name="public_website"),

    path("robots.txt", sf.robots_txt, name="public_robots_txt"),
    path("sitemap.xml", sf.sitemap_xml, name="public_sitemap_xml"),
    path("favicon.ico", sf.favicon, name="public_favicon"),

    # Catalogue (referral card pages)
    path("shop/", ref.shop, name="public_all_products"),
    path("products/", ref.shop, name="public_products_index"),
    path("categories/", ref.categories, name="public_categories"),
    path("category/<slug:category_slug>/", ref.category_detail, name="public_category_detail"),

    # Reserved product sub-routes (actions/JSON  reused) precede the slug route
    path("products/wa/<str:product_id>/", sf.product_whatsapp, name="public_product_whatsapp"),
    path("products/<str:product_id>/review/", sf.submit_product_review, name="submit_product_review"),
    path("products/<str:product_id>/review/delete/", sf.review_delete, name="public_review_delete"),
    path("products/<slug:product_slug>/", ref.product_detail, name="public_product_detail"),

    # Content pages (referral card pages)
    path("about/", ref.about, name="public_about"),
    path("certificates/", ref.certificates, name="public_certificates"),
    path("contact/", ref.enquiry, name="public_enquiry"),
    # /blogs/ is the one blog URL (as on the owner store); /blog/ 301s there.
    path("blogs/", ref.blogs, name="public_blogs"),
    path("blogs/<slug:blog_slug>/", ref.blog_detail, name="public_blog_detail"),
    path("blog/", RedirectView.as_view(pattern_name="public_blogs", permanent=True, query_string=True), name="public_blogs_alias"),
    path("blog/<slug:blog_slug>/", RedirectView.as_view(pattern_name="public_blog_detail", permanent=True, query_string=True), name="public_blog_detail_alias"),
    path("about-us/", ref.about, name="public_about_us"),
    path("enquiry/", ref.enquiry, name="public_enquiry_alias"),
    path("core-pages/", ref.core_pages_index, name="public_core_pages"),

    # Policies (referral card pages)
    path("privacy-policy/", ref.privacy_policy, name="privacy_policy_page"),
    path("terms-conditions/", ref.terms_conditions, name="public_terms"),
    path("refund-policy/", ref.refund_policy, name="public_refund"),
    path("shipping-policy/", ref.shipping_policy, name="public_shipping"),
    path("custom-policy/", ref.custom_policy, name="public_custom_policy"),
    path("policies/<slug:policy_slug>/", ref.custom_policy_page, name="public_policy_page"),

    # JSON APIs (reused)
    path("api/track/", sf.track, name="public_track"),
    path("api/products/suggest/", sf.api_product_suggest, name="public_product_suggest"),
    path("api/product/<str:product_id>/variants/", sf.api_product_variants, name="public_product_variants"),
    path("api/product/<str:product_id>/price-history/", sf.api_product_price_history, name="public_product_price_history"),
    path("api/pincode/", sf.pincode_serviceability, name="public_pincode_serviceability"),

    # Cart / checkout / webhooks (reused; shared templates re-skin via store_base)
    path("cart/", sf.cart_view, name="public_cart"),
    path("cart/data/", sf.cart_data, name="public_cart_data"),
    path("cart/add/<str:product_id>/", sf.cart_add, name="public_cart_add"),
    path("cart/update/", sf.cart_update_body, name="public_cart_update_body"),
    path("cart/update/<str:product_id>/", sf.cart_update, name="public_cart_update"),
    path("cart/remove/<str:product_id>/", sf.cart_remove, name="public_cart_remove"),
    path("cart/coupon/", sf.cart_apply_coupon, name="public_cart_coupon"),
    path("checkout/", ref.checkout, name="public_checkout"),
    path("checkout/place/", sf.checkout_place, name="public_checkout_place"),
    path("checkout/verify/", sf.payment_verify, name="public_payment_verify"),
    path("webhooks/razorpay/", sf.webhook_razorpay, name="public_webhook_razorpay"),
    path("webhooks/cashfree/", sf.webhook_cashfree, name="public_webhook_cashfree"),
    path("webhooks/tracking/", sf.webhook_courier, name="public_webhook_courier"),
    path("webhooks/tracking/<str:token>/", sf.webhook_courier_token, name="public_webhook_courier_token"),
    path("wishlist/add/<str:product_id>/", sf.wishlist_add, name="public_wishlist_add"),
    path("wishlist/remove/<str:product_id>/", sf.wishlist_remove, name="public_wishlist_remove"),
    path("account/", ref.account, name="public_user_account"),
    path("account/orders/", sf.user_orders, name="public_user_orders"),
    path("account/send-otp/", sf.account_send_otp, name="public_account_send_otp"),
    path("account/verify-otp/", sf.account_verify_otp, name="public_account_verify_otp"),
    path("account/logout/", sf.account_logout, name="public_account_logout"),
    path("account/profile/", sf.account_profile_save, name="public_account_profile"),
    path("account/email/send-otp/", sf.account_email_send_otp, name="public_account_email_otp"),
    path("account/email/verify/", sf.account_email_verify, name="public_account_email_verify"),
    path("account/addresses/save/", sf.account_address_save, name="public_account_address_save"),
    path("account/addresses/<uuid:address_id>/delete/", sf.account_address_delete, name="public_account_address_delete"),
    path("account/delete/send-otp/", sf.account_delete_send_otp, name="public_account_delete_otp"),
    path("account/delete/confirm/", sf.account_delete_confirm, name="public_account_delete_confirm"),
    path("orders/<str:order_id>/confirmation/", sf.order_confirmation, name="public_order_confirmation"),
    path("account/orders/<str:order_id>/invoice/", sf.order_invoice, name="public_order_invoice"),
    path("account/orders/<str:order_id>/request/", sf.account_order_request, name="public_order_request"),
    path("newsletter/subscribe/", sf.subscribe_newsletter, name="subscribe_newsletter"),

    path("_owner/edit/<uuid:site_id>/", sf.owner_noop, name="edit_website"),
    path("_owner/apply/<uuid:site_id>/", sf.owner_noop, name="apply_enhanced_template"),

    # Core pages live at the bare slug  must stay LAST.
    path("<slug:page_slug>/", ref.core_page, name="public_core_page"),
    path("<slug:page_slug>/<slug:sub_slug>/", ref.core_page, name="public_core_subpage"),
    path("<slug:page_slug>/<slug:sub_slug>/<slug:child_slug>/", ref.core_page, name="public_core_child"),
]

handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
