"""Presentable default content for a freshly created store.

The single biggest gap in the reference tool was that a new site came out full of
literal placeholders  ``<Insert FAQ question here>``, ``<Customer Name>``,
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

# Presentable defaults for the "why choose us" points (4) and the home FAQ (8).
# Module-level so both the seed (below) and the storefront's pre-render padding
# (``website_filters.padded_items``) share one source: a store whose stored
# content has fewer items is topped up to these counts so the section still
# reads as complete, without re-seeding. No em dashes in any copy.
DEFAULT_WHY_CHOOSE_US = [
    {"title": "Quality first", "description": "Every product is checked before it ships.", "icon": "fa-award"},
    {"title": "Fast delivery", "description": "Orders dispatched quickly and tracked end to end.", "icon": "fa-truck"},
    {"title": "Here to help", "description": "Real people, ready to answer your questions.", "icon": "fa-headset"},
    {"title": "Secure checkout", "description": "Encrypted payments and trusted gateways, every time.", "icon": "fa-shield"},
]

DEFAULT_FAQS = [
    {"question": "How long does delivery take?", "answer": "Most orders arrive within 3 to 7 business days, depending on your location."},
    {"question": "What is your return policy?", "answer": "You can return most items within 7 days of delivery. See our refund policy for details."},
    {"question": "Do you ship nationwide?", "answer": "Yes, we ship across the country. Enter your pincode at checkout to confirm serviceability."},
    {"question": "Which payment methods do you accept?", "answer": "We accept UPI, cards, net banking and cash on delivery where available."},
    {"question": "Can I track my order?", "answer": "Yes. As soon as your order ships we email you a tracking link so you can follow it to your door."},
    {"question": "Are your products covered by a warranty?", "answer": "Eligible products carry a manufacturer warranty; the details are listed on each product page."},
    {"question": "Do you offer exchanges?", "answer": "Yes. You can exchange an eligible item within 7 days if it arrives damaged or the size is not right."},
    {"question": "How can I contact you?", "answer": "Use the contact page or email us, and we usually reply within one business day."},
]

# Product-page FAQ defaults for wholesale/B2B stores  the questions a trade
# buyer usually has. Shown on a B2B product page when the owner hasn't added
# their own FAQs for that product.
DEFAULT_B2B_FAQS = [
    {"question": "What is the minimum order quantity (MOQ)?", "answer": "MOQs vary by product and are shown on each item's page. Send an enquiry with your requirement and we'll confirm the minimum for your order."},
    {"question": "Do you offer bulk or wholesale pricing?", "answer": "Yes. Share the quantity you need through the enquiry form and we'll send a tiered price quote."},
    {"question": "How do I request a quote?", "answer": "Use the Enquiry button on any product. Our team responds with pricing and lead time, usually within one business day."},
    {"question": "Will I receive a GST tax invoice?", "answer": "Yes. Every order ships with a GST-compliant tax invoice so you can claim input credit."},
    {"question": "What are your payment terms?", "answer": "We accept bank transfer, UPI and cards. Credit terms can be discussed for regular trade accounts."},
    {"question": "What is the lead time for bulk orders?", "answer": "In-stock items dispatch in 2–3 business days; made-to-order and large quantities are confirmed at the time of quotation."},
    {"question": "Do you support custom, private-label or OEM orders?", "answer": "Yes, for eligible products. Share your specifications in an enquiry and we'll confirm feasibility and the MOQ."},
    {"question": "How is freight handled for large orders?", "answer": "Freight is quoted per destination and volume. We ship pan-India and arrange bulk logistics for large consignments."},
]


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
            f"every order with responsive support, so you can sell with confidence."
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
                "overlay_opacity": 4,
                "overlay_color": "#000000",
                # Colours left blank are automatic: readable on whichever hero
                # style the owner picks (storefront.hero_colors).
                "title_color": "",
                "description_color": "",
                "button_bg_color": "",
                "button_text_color": "",
            }
        ],
        # Store-wide announcement bar (hidden when blank).
        "announcement_text": "",
        # About (homepage about block)
        "about_section_title": "About us",
        "about_title": f"About {name}",
        "about_content": about_body,
        "about_section_content": about_body,
        "about_image": "",
        "about_image_alt": "",
        # About PAGE (about-us.html  editorial layout)
        "aus_brand_section_label": "Our story",
        "aus_brand_section_title": f"The story behind {name}",
        "aus_brand_section_description": about_body,
        "aus_brand_heading": name,
        "aus_brand_subheading": "Built for people who care about quality.",
        "aus_brand_description": about_body,
        "aus_brand_main_image": "",
        "aus_brand_secondary_image": "",
        "editorial_banner_image": "",
        "editorial_intro_1": about_body,
        "editorial_intro_2": (
            "Every product we make is chosen and checked with care, so what arrives is "
            "exactly what you hoped for."
        ),
        "cowork_block1_title": "Made with intention",
        "cowork_block1_desc": "We keep our range tight and considered, so every item earns its place.",
        "cowork_block1_image": "",
        "cowork_block2_title": "Here when you need us",
        "cowork_block2_desc": "Real people, quick answers, and a promise to make things right.",
        "cowork_block2_image": "",
        "cowork_block2_quote": "Quality you can feel, service you can trust.",
        "cowork_block2_tags": "Quality, Support, Trust",
        "cowork_metric1_value": "10k+",
        "cowork_metric1_label": "Happy customers",
        "cowork_metric2_value": "4.8/5",
        "cowork_metric2_label": "Average rating",
        "cowork_metric3_value": "24h",
        "cowork_metric3_label": "Typical reply time",
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
        "why_choose_us": [dict(x) for x in DEFAULT_WHY_CHOOSE_US],
        # Testimonials
        "testimonials_title": "What our customers say",
        "testimonials_subtitle": "Real feedback from real people.",
        "testimonials": [
            {"name": "A. Sharma", "role": "Verified buyer", "content": "Exactly as described and delivered on time. I'll be back.", "rating": 5, "image": ""},
            {"name": "R. Iyer", "role": "Verified buyer", "content": "Great quality for the price. Highly recommend.", "rating": 5, "image": ""},
            {"name": "M. Khan", "role": "Verified buyer", "content": "Smooth ordering and friendly support. Five stars.", "rating": 5, "image": ""},
            {"name": "P. Nair", "role": "Verified buyer", "content": "Packaging was lovely and the product feels premium. Very happy.", "rating": 5, "image": ""},
            {"name": "S. Gupta", "role": "Verified buyer", "content": "Fast shipping and the quality exceeded my expectations.", "rating": 5, "image": ""},
            {"name": "D. Mehta", "role": "Verified buyer", "content": "Ordered twice now, consistent quality both times. Trustworthy.", "rating": 5, "image": ""},
            {"name": "K. Reddy", "role": "Verified buyer", "content": "Support answered within minutes and sorted my query instantly.", "rating": 5, "image": ""},
            {"name": "N. Joshi", "role": "Verified buyer", "content": "Exactly what I wanted, delivered ahead of schedule. Recommended.", "rating": 5, "image": ""},
        ],
        # FAQ  a full set of eight, so the home FAQ section is complete out of
        # the box (the design shows up to eight). No em dashes in any copy.
        "faq_title": "Frequently asked questions",
        "faq_subtitle": "Everything you need to know.",
        "faqs_subtitle": "Everything you need to know.",
        "faqs": [dict(x) for x in DEFAULT_FAQS],
        # Reels / short-video strip (shared video_section.html). Left empty on a
        # real store so it falls back to reference stills; the demo store fills
        # this with playable vertical videos.
        "video_section": {
            "title": "Join the tribe",
            "position": "bottom",
            "videos": [],
        },
        # CTA band
        "cta_title": "Ready to get started?",
        "cta_subtitle": "Browse the full range and find your next favourite.",
        # Footer / contact / social
        "footer_description": f"{name}. Quality products, delivered with care.",
        "social_links": {"facebook": "", "instagram": "", "twitter": ""},
        # Heading + subtext above the enquiry form on the Contact page.
        "contact_form_title": "Let's talk",
        "contact_form_subtitle": (
            "Questions about an order, a product or anything else? Send us a message "
            f"and the {name} team will get back to you shortly."
        ),
        "contact_info": {
            "email": site.support_email or "",
            "contact_email": site.support_email or "",
            "phone": site.support_phone or "",
            "mobile_number": site.support_phone or "",
            "whatsapp_number": "",
            "address": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "country": "India",
            # The person customers "hear from" on the contact page.
            "ceo_name": "",
            "ceo_designation": "Founder",
            "ceo_profile_picture": "",
            "ceo_message": (
                f"Thanks for visiting {name}. We'd love to hear from you, so reach out any "
                "time and we'll get back to you quickly."
            ),
            # Office hours.
            "office_start_day": "Monday",
            "office_end_day": "Saturday",
            "office_start_time": "09:30",
            "office_end_time": "18:30",
            "office_holidays": "Sunday",
            "map_location": "",
        },
        # Contact FAQ (components/contact_faq.html)
        "contact_faqs_title": "Frequently asked ",
        "contact_faqs_title_suffix": "questions",
        "contact_faqs_subtitle": "Common questions about reaching us and getting help.",
        "contact_faqs": [
            {"question": "How soon will I get a reply?", "answer": "We usually respond within one business day."},
            {"question": "Can I visit in person?", "answer": "Reach out first and we'll be glad to arrange a time."},
        ],
        # SEO  one set for the whole site by default; the owner can switch to
        # per-page in the editor, where blank per-page fields fall back to this.
        "seo_mode": "same",
        "seo": {
            "meta_title": name,
            "meta_description": hero_desc,
            "keywords": "",
        },
        # Policy pages  real starter text, editable by the owner.
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
            "<h2>Shipping Policy</h2><p>Orders are processed within 1 to 2 business days and "
            "delivered within 3 to 7 business days depending on your location. Tracking is "
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
