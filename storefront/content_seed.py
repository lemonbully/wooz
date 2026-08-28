"""Presentable default content for a freshly created store.

The single biggest gap in the reference tool was that a new site came out full of
literal placeholders — ``<Insert FAQ question here>``, ``<Customer Name>``,
``via.placeholder.com`` images. OneWooz's promise is one click to a *presentable*
store, so this returns real, coherent copy for every section the storefront
renders. Images are left empty on purpose: the templates fall back to bundled
neutral placeholders, and the owner replaces copy and imagery in the editor.

Everything here is keyed exactly as the ported templates read it
(``site.content.*``), and the demo product/category cards carry the ``slug`` and
``product_id`` the homepage links need so nothing 404s before the catalogue is
filled in.
"""

from __future__ import annotations

from tenancy.models import Site

# One restrained accent, matching the OneWooz design language, used as the
# storefront's primary colour until the owner picks their own.
_PALETTE = {
    "primary_color": "#4b2bf5",
    "secondary_color": "#1e293b",
    "accent_color": "#f59e0b",
    "text_color": "#374151",
    "bg_color": "#ffffff",
    "bg_alt_color": "#f9fafb",
}


def _demo_product(title, price, mrp, slug):
    # Demo cards link to the shop until the owner curates real products; a
    # present ``url`` keeps the homepage links sensible and every key the card
    # reads exists so rendering never raises.
    return {
        "product_id": slug,
        "slug": slug,
        "title": title,
        "price": price,
        "mrp": mrp,
        "original_price": mrp,
        "currency": "INR",
        "image": "",
        "image1": "",
        "url": "/shop/",
        "alt_text": title,
        "on_sale": bool(mrp and price and float(mrp) > float(price)),
        "is_new": False,
        "in_stock": True,
    }


def _demo_category(title, slug):
    return {"title": title, "name": title, "slug": slug, "image": "", "url": f"/category/{slug}/"}


def default_content(site: Site) -> dict:
    """Build a presentable content blob for ``site``."""
    name = site.name or "Your store"
    is_b2b = site.is_b2b

    if is_b2b:
        hero_title = f"Wholesale, made simple with {name}"
        hero_desc = "Quality products at trade prices, ready to ship in bulk. Request a quote and our team will get back within one business day."
        cta_label = "Request a quote"
        about_body = (
            f"{name} supplies retailers and businesses with dependable products "
            f"and dependable service. We hold stock, price transparently, and back "
            f"every order with responsive support — so you can sell with confidence."
        )
    else:
        hero_title = f"Welcome to {name}"
        hero_desc = "Thoughtfully made products, delivered to your door. Discover what everyone's talking about."
        cta_label = "Shop now"
        about_body = (
            f"{name} was built around a simple idea: great products, honest prices, "
            f"and a shopping experience that respects your time. Everything we make "
            f"is designed to be used and loved every day."
        )

    return {
        "site_name": name,
        "websiteName": name,
        **_PALETTE,
        "hero_banner_style": "style1",
        "hero_banners": [
            {
                "title": hero_title,
                "description": hero_desc,
                "button_text": cta_label,
                "button_link_type": "shop",
                "button_url": "/shop/",
                "image": "",
                "mobile_image": "",
                "alt_text": f"{name} banner",
                "overlay_opacity": 40,
                "title_color": "#ffffff",
                "description_color": "#e5e7eb",
                "button_bg_color": "#111827",
                "button_text_color": "#ffffff",
            }
        ],
        # About
        "about_section_title": "About us",
        "about_title": f"About {name}",
        "about_content": about_body,
        "about_section_content": about_body,
        "about_image": "",
        "about_image_alt": "",
        # Video section (hidden until the owner adds a URL, but every key the
        # templates read as a |default: argument must exist so rendering a store
        # with no video never raises).
        "video_url": "",
        "video_title": "See us in action",
        "video_thumbnail": "",
        "video_thumbnail_alt": "",
        "video_description": "",
        "video_extended_description": "",
        # Blog strip labels (blog listing itself is a later phase).
        "blog_title": "From our blog",
        "blog_subtitle": "News, stories and updates.",
        # Categories strip (home.html reads the category_section_* keys)
        "categories_title": "Shop by category",
        "categories_subtitle": "Browse our most popular collections.",
        "category_section_style": "style1",
        "category_section_heading": "Shop by category",
        "category_section_subheading": "Browse our most popular collections.",
        "category_section_heading_s3": "Shop by category",
        "category_section_subheading_s3": "Browse our most popular collections.",
        "best_sellers_subtitle": "Our most-loved products, chosen by customers like you.",
        "categories": [
            _demo_category("New arrivals", "new-arrivals"),
            _demo_category("Best sellers", "best-sellers"),
            _demo_category("Essentials", "essentials"),
        ],
        # Featured products
        "top_seller_title": "Best sellers",
        "top_seller_subtitle": "Our customers' favourites.",
        "top_seller_products": [
            _demo_product("Signature product", "1499", "1999", "signature-product"),
            _demo_product("Everyday essential", "799", "999", "everyday-essential"),
            _demo_product("Premium pick", "2499", "2999", "premium-pick"),
            _demo_product("Starter choice", "499", "699", "starter-choice"),
        ],
        "new_products_title": "New arrivals",
        "new_products_subtitle": "Fresh in, just for you.",
        "new_products": [
            _demo_product("Latest release", "1299", "1599", "latest-release"),
            _demo_product("Season special", "999", "1299", "season-special"),
            _demo_product("Limited edition", "1799", "2199", "limited-edition"),
        ],
        # Why choose us
        "why_choose_us_subtitle": "What makes us different.",
        "why_choose_us": [
            {"title": "Quality first", "description": "Every product is checked before it ships.", "icon": "fa-award"},
            {"title": "Fast delivery", "description": "Orders dispatched quickly and tracked end to end.", "icon": "fa-truck"},
            {"title": "Here to help", "description": "Real people, ready to answer your questions.", "icon": "fa-headset"},
        ],
        # Testimonials
        "testimonials_title": "What our customers say",
        "testimonials_subtitle": "Real feedback from real people.",
        "testimonials": [
            {"name": "A. Sharma", "role": "Verified buyer", "content": "Exactly as described and delivered on time. I'll be back.", "rating": 5, "image": ""},
            {"name": "R. Iyer", "role": "Verified buyer", "content": "Great quality for the price. Highly recommend.", "rating": 5, "image": ""},
            {"name": "M. Khan", "role": "Verified buyer", "content": "Smooth ordering and friendly support. Five stars.", "rating": 5, "image": ""},
        ],
        # FAQ
        "faq_title": "Frequently asked questions",
        "faq_subtitle": "Everything you need to know.",
        "faqs_subtitle": "Everything you need to know.",
        "faqs": [
            {"question": "How long does delivery take?", "answer": "Most orders are delivered within 3–7 business days, depending on your location."},
            {"question": "What is your return policy?", "answer": "You can return most items within 7 days of delivery. See our refund policy for details."},
            {"question": "Do you ship nationwide?", "answer": "Yes — we ship across the country. Enter your pincode at checkout to confirm serviceability."},
            {"question": "How can I contact you?", "answer": "Use the contact page or email us; we usually reply within one business day."},
        ],
        # CTA band
        "cta_title": "Ready to get started?",
        "cta_subtitle": "Browse the full range and find your next favourite.",
        # Footer / contact / social
        "footer_description": f"{name} — quality products, delivered with care.",
        "social_links": {"facebook": "", "instagram": "", "twitter": ""},
        "contact_info": {
            "email": site.support_email or "",
            "phone": site.support_phone or "",
            "address": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "country": "India",
        },
        # SEO
        "seo": {
            "meta_title": name,
            "meta_description": hero_desc,
            "keywords": "",
        },
        # Policy pages — real starter text, editable by the owner.
        "privacy_policy": (
            f"<h2>Privacy Policy</h2><p>{name} respects your privacy. We collect only "
            f"the information needed to process your orders and improve your experience, "
            f"and we never sell your personal data. Contact us for any privacy request.</p>"
        ),
        "terms_conditions": (
            f"<h2>Terms &amp; Conditions</h2><p>By using {name}, you agree to purchase "
            f"products in good faith and to the pricing and availability shown at the "
            f"time of order. These terms may be updated from time to time.</p>"
        ),
        "refund_policy": (
            "<h2>Refund Policy</h2><p>If something isn't right, you may request a return "
            "or refund within 7 days of delivery for eligible items. Refunds are issued to "
            "the original payment method once the return is received.</p>"
        ),
        "shipping_policy": (
            "<h2>Shipping Policy</h2><p>Orders are processed within 1–2 business days and "
            "delivered within 3–7 business days depending on your location. Tracking is "
            "shared as soon as your order ships.</p>"
        ),
        "enquiry_form_mode": "contact",
        "enable_product_hover_swap": True,
        # Call-to-action buttons (about-us page and hero fallbacks).
        "hero_title": hero_title,
        "hero_subtitle": hero_desc,
        "hero_button": {"url": "/shop/", "label": cta_label},
        "about_button": {"url": "/about/", "label": "Learn more"},
        "cta_main_button": {"url": "/shop/", "label": cta_label},
        # Feature highlights (rendered on the About page).
        "features_title": "Why shop with us",
        "features_subtitle": "What you can count on.",
        "features": [
            {"icon": "award", "title": "Quality first", "description": "Every product is checked before it ships."},
            {"icon": "truck", "title": "Fast delivery", "description": "Dispatched quickly and tracked end to end."},
            {"icon": "headset", "title": "Here to help", "description": "Real people, ready to answer your questions."},
        ],
    }
