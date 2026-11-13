"""Schema.org structured data (JSON-LD) for a generated storefront.

Implements the per-page structured-data specification (home, contact, about,
blog, blog post, all products, all categories, category, product), extended
to the storefront's remaining indexable pages (certificates, content pages,
policies) in the same style.

The model:

* **One global identity.** The store (``#organization``), the site
  (``#website``) and the logo (``#logo``) are described in full once, on the
  home page. Every other page carries only its own entities and *references*
  those by ``@id``, so there is never a second, conflicting organization.
* **Each page describes itself.** Home → ``OnlineStore`` + ``WebSite`` +
  ``ImageObject`` + ``WebPage`` (+ the featured-products ``ItemList``).
  Contact / About → ``ContactPage`` / ``AboutPage`` + ``BreadcrumbList``.
  Blog → ``CollectionPage`` + ``ItemList`` + ``BreadcrumbList``. Blog post →
  ``BlogPosting`` + ``BreadcrumbList``. Listings → ``CollectionPage`` +
  ``ItemList`` + ``BreadcrumbList``. Product → ``Product`` + ``Brand`` +
  ``Offer`` (+ genuine ``AggregateRating`` / ``Review``) + ``BreadcrumbList``;
  a product with variations is a ``ProductGroup`` whose ``hasVariant`` lists
  one ``Product`` + ``Offer`` per stored combination.
* **Parent company.** When the owner names the organization that owns the
  brand (Edit content → Brand), it is an ``Organization`` (``#operator``) and
  the store carries ``parentOrganization`` → it, on the home and About pages.
* **Site search.** The home page's ``WebSite`` declares a ``SearchAction`` on
  the catalogue's own ``?q=`` search.
* **Only what is real.** Every value comes from the current database / page
  state: no invented ratings, prices, availability, SKUs, GTINs, authors or
  dates; no FAQ markup; no shipping promise (none is configured); a return
  policy only from the product's own return type and window (the same fields
  its page shows; a "Non returnable" badge means 0 days). Missing facts are
  omitted.
* **Lists mirror the page.** Every ``ItemList`` is built from the items the
  view actually rendered, and ``numberOfItems`` is the length of that list.
* **Breadcrumbs mirror the visible navigation**; the last crumb (the current
  page) carries no ``item`` URL.
* **D2C and B2B diverge where the business does** (decided by the theme, as
  cart-vs-enquiry is): ``template2`` is an ``OnlineStore`` selling retail;
  ``template1`` is an ``OnlineBusiness`` (+ ``WholesaleStore`` with a real
  address) whose offers are scoped to business buyers from the minimum order
  quantity, with its certifications on the organization.
* **Server-rendered, never fatal.** Printed into the initial HTML by
  ``theme_base.html``; any error returns ``""`` rather than breaking a page.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation

from django.urls import NoReverseMatch, reverse

from .seo_pages import _text, _walk_core

logger = logging.getLogger(__name__)

SCHEMA = "https://schema.org"

# Cap on list entries, so a huge catalogue never bloats the HTML.
# ``numberOfItems`` always states the length of the list actually emitted.
_LIST_LIMIT = 100
_REVIEW_LIMIT = 10
_DESC_LIMIT = 5000

_COUNTRY_NAMES = {
    "IN": "India", "US": "United States", "GB": "United Kingdom", "AE": "United Arab Emirates",
    "CA": "Canada", "AU": "Australia", "SG": "Singapore", "NP": "Nepal", "BD": "Bangladesh",
    "LK": "Sri Lanka", "DE": "Germany", "FR": "France",
}

# Owner-written specification rows that hold genuine identifiers.
_SPEC_BRAND = {"brand", "brand name"}
_SPEC_GTIN = {"gtin", "ean", "upc", "barcode", "isbn", "ean/upc"}
_SPEC_MPN = {"mpn", "part number", "manufacturer part number"}

# Variation names that map onto a schema.org Product property (and so may be
# listed in ProductGroup.variesBy). Any other variation (e.g. "Pack of") rides
# on each variant as an additionalProperty instead.
_VARIES_BY = {
    "size": "size", "sizes": "size",
    "colour": "color", "color": "color", "colours": "color", "colors": "color",
    "material": "material",
    "pattern": "pattern",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _s(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _first(*values) -> str:
    for v in values:
        s = _s(v)
        if s:
            return s
    return ""


def _plain(html, limit) -> str:
    """Plain text from owner HTML, word-bounded to ``limit``."""
    return _text(html, limit) if _s(html) else ""


def _prune(value):
    """Drop empty values (``None``, ``""``, ``[]``, ``{}``) at every depth. Zero
    and ``False`` are meaningful and stay."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = _prune(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        # A node left with nothing but its type carries no information.
        if out and set(out) <= {"@type", "@context"}:
            return {}
        return out
    if isinstance(value, (list, tuple)):
        items = [_prune(v) for v in value]
        return [v for v in items if not (v is None or v == "" or v == [] or v == {})]
    return value


def _money(value) -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    return f"{d.quantize(Decimal('0.01'))}" if d > 0 else ""


def _iso(dt) -> str:
    try:
        return dt.isoformat() if dt else ""
    except Exception:
        return ""


def _url_for(name, **kwargs) -> str:
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        return ""


def _abs(request, url) -> str:
    """An absolute URL for anything the store stored: full URLs pass through,
    protocol-relative get https, root paths resolve against this host, and bare
    storage keys resolve under MEDIA_URL."""
    v = _s(url)
    if not v or v.lower().startswith("data:"):
        return ""
    if v.lower().startswith(("http://", "https://")):
        return v
    if v.startswith("//"):
        return "https:" + v
    if not v.startswith("/"):
        from .templatetags.website_filters import _media_url

        v = _media_url() + v.lstrip("/")
    try:
        return request.build_absolute_uri(v)
    except Exception:
        return ""


def _abs_route(request, name, **kwargs) -> str:
    path = _url_for(name, **kwargs)
    return _abs(request, path) if path else ""


def _file_url(request, field) -> str:
    try:
        return _abs(request, field.url) if field else ""
    except Exception:
        return ""


def _phone(raw, country) -> str:
    """Owner-typed phone → E.164 (``+916009112288``) where unambiguous."""
    s = _s(raw)
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        return "+" + digits if digits else ""
    if country == "IN":
        if len(digits) == 10:
            return "+91" + digits
        if len(digits) == 12 and digits.startswith("91"):
            return "+" + digits
        if len(digits) == 11 and digits.startswith("0"):
            return "+91" + digits[1:]
    return s


def _json(graph) -> str:
    """Serialise for an inline ``<script>``: compact, UTF-8, and with ``<``,
    ``>`` and ``&`` escaped so owner text can never close the script element."""
    raw = json.dumps(graph, ensure_ascii=False, separators=(",", ":"), default=str)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(chr(0x2028), "\\u2028")
        .replace(chr(0x2029), "\\u2029")
    )


# --------------------------------------------------------------------------
# The builder
# --------------------------------------------------------------------------

class _Graph:
    """Builds the ``@graph`` for one request."""

    def __init__(self, request, site, content, seo, ctx):
        self.request = request
        self.site = site
        self.c = content or {}
        self.seo = seo or {}
        self.ctx = ctx or {}
        # The theme decides the business surface, exactly as it decides
        # cart-vs-enquiry on the product page.
        self.b2b = getattr(site, "template_dir", "") == "template1"
        self.country = (_s(getattr(site, "country", "")) or "IN").upper()
        self.currency = _s(getattr(site, "currency", "")) or "INR"
        self.lang = f"en-{self.country}"
        self.name = _first(self.c.get("site_name"), self.c.get("websiteName"), site.name)

        self.root = _abs_route(request, "public_website") or _abs(request, "/")
        self.org_id = f"{self.root}#organization"
        self.website_id = f"{self.root}#website"
        self.logo_id = f"{self.root}#logo"
        self.operator_id = f"{self.root}#operator"
        # The organization that owns the brand (Edit content → Brand), if any.
        parent = _s(self.c.get("parent_organization"))[:200]
        self.parent_name = "" if parent.casefold() == _s(self.name).casefold() else parent

        self.page_url = _first(self.seo.get("canonical_url"), self.ctx.get("canonical_url"))
        self.page_id = f"{self.page_url}#webpage"
        self.breadcrumb_id = f"{self.page_url}#breadcrumb"

    # -- facts ----------------------------------------------------------

    @property
    def contact(self) -> dict:
        ci = self.c.get("contact_info")
        return ci if isinstance(ci, dict) else {}

    def email(self) -> str:
        return _first(self.contact.get("contact_email"), self.contact.get("email"), self.site.support_email)

    def telephone(self) -> str:
        return _phone(_first(self.contact.get("mobile_number"), self.contact.get("phone"), self.site.support_phone), self.country)

    def address(self):
        ci = self.contact
        street = _plain(ci.get("address"), 300)
        locality, region, postal = _s(ci.get("city")), _s(ci.get("state")), _s(ci.get("postal_code"))
        if not (street or locality or postal):
            return None
        country_text = _s(ci.get("country"))
        country = self.country if (not country_text or country_text.lower() in ("india", "in", "bharat")) else country_text
        return {
            "@type": "PostalAddress",
            "streetAddress": street,
            "addressLocality": locality,
            "addressRegion": region,
            "postalCode": postal,
            "addressCountry": country,
        }

    def same_as(self):
        """Official profiles only: the social links the owner entered."""
        links = self.c.get("social_links")
        out = []
        for v in (links.values() if isinstance(links, dict) else []):
            u = _s(v)
            if u.lower().startswith(("http://", "https://")) and u not in out:
                out.append(u)
        return out

    def contact_points(self):
        email, phone = self.email(), self.telephone()
        points = []
        if email or phone:
            points.append({
                "@type": "ContactPoint",
                # A trade catalogue's main line is its sales desk.
                "contactType": "sales" if self.b2b else "customer support",
                "email": email,
                "telephone": phone,
                "areaServed": self.country,
                "availableLanguage": self.lang,
            })
        # Further channels only when the owner configured them (shown on the
        # contact page): WhatsApp, and the grievance officer Indian e-commerce
        # rules require.
        wa = re.sub(r"\D", "", _s(self.contact.get("whatsapp_number")))
        if len(wa) >= 10:
            full = wa if len(wa) > 10 else ("91" + wa if self.country == "IN" else wa)
            points.append({
                "@type": "ContactPoint",
                "contactType": "WhatsApp",
                "telephone": "+" + full,
                "url": f"https://wa.me/{full}",
                "areaServed": self.country,
                "availableLanguage": self.lang,
            })
        go = self.c.get("grievance_officer")
        if isinstance(go, dict) and (_s(go.get("email")) or _s(go.get("mobile"))):
            points.append({
                "@type": "ContactPoint",
                "contactType": "grievance officer",
                "name": _s(go.get("name")),
                "email": _s(go.get("email")),
                "telephone": _phone(go.get("mobile"), self.country),
                "areaServed": self.country,
                "availableLanguage": self.lang,
            })
        return points

    def logo_url(self) -> str:
        return _abs(self.request, _first(self.c.get("desktop_logo"), self.c.get("mobile_logo")))

    def brand_description(self) -> str:
        brand = self.c.get("seo") if isinstance(self.c.get("seo"), dict) else {}
        return _first(
            _plain(brand.get("meta_description"), 300),
            _plain(self.c.get("footer_description"), 300),
            _plain(self.c.get("about_content"), 300),
            getattr(self.site, "tagline", ""),
        )

    # -- reusable nodes -------------------------------------------------

    def org_types(self):
        if self.b2b:
            return ["OnlineBusiness", "WholesaleStore"] if self.address() else "OnlineBusiness"
        return "OnlineStore"

    def parent_ref(self):
        return {"@id": self.operator_id} if self.parent_name else None

    def operator(self):
        """The company that owns the brand, as the owner named it."""
        if not self.parent_name:
            return None
        return {"@type": "Organization", "@id": self.operator_id, "name": self.parent_name}

    def breadcrumbs(self, trail):
        """``trail``: ``[(name, url), …]`` after Home. The last entry is the
        current page and, per the visible breadcrumb, carries no ``item``."""
        items = [("Home", self.root)] + [(n, u) for n, u in trail if _s(n)]
        out = []
        for i, (n, u) in enumerate(items, start=1):
            node = {"@type": "ListItem", "position": i, "name": n}
            if u and i < len(items):
                node["item"] = u
            out.append(node)
        return {"@type": "BreadcrumbList", "@id": self.breadcrumb_id, "itemListElement": out}

    def page(self, page_type, *, name, description=None, main_entity=None, about=None):
        return {
            "@type": page_type,
            "@id": self.page_id,
            "url": self.page_url,
            "name": name,
            "description": description,
            "isPartOf": {"@id": self.website_id},
            "about": about,
            "mainEntity": main_entity,
            "inLanguage": self.lang,
        }

    def item_list(self, list_id, name, entries):
        """``entries``: ``[(url, name), …]`` in rendered order."""
        items = []
        for url, label in entries:
            if url and len(items) < _LIST_LIMIT:
                items.append({"@type": "ListItem", "position": len(items) + 1, "url": url, "name": label})
        if not items:
            return None
        return {
            "@type": "ItemList",
            "@id": list_id,
            "name": name,
            "itemListOrder": f"{SCHEMA}/ItemListUnordered",
            "numberOfItems": len(items),
            "itemListElement": items,
        }

    def product_entries(self, products):
        out = []
        for p in products or []:
            raw = getattr(p, "_p", p)
            slug = _s(getattr(raw, "slug", ""))
            if slug:
                out.append((_abs_route(self.request, "public_product_detail", product_slug=slug), _s(getattr(raw, "title", ""))))
        return out

    def certification_nodes(self):
        """The owner's certificates as ``Certification`` nodes with stable
        ``@id``s, the uploaded document attached as ``associatedMedia``."""
        from .certificates import is_pdf_url, public_certificates

        page = _abs_route(self.request, "public_certificates")
        nodes = []
        for i, cert in enumerate(public_certificates(self.c)[:_LIST_LIMIT], start=1):
            thumb = _abs(self.request, cert["thumb"])
            doc = _abs(self.request, cert["document"] or cert["file"])
            media = None
            if doc:
                media = {
                    "@type": "DigitalDocument" if is_pdf_url(doc) else "ImageObject",
                    "name": f"{cert['title']} certificate",
                    "contentUrl": doc,
                    "encodingFormat": "application/pdf" if is_pdf_url(doc) else None,
                }
            nodes.append({
                "@type": "Certification",
                "@id": f"{page}#certificate-{i}",
                "name": cert["title"],
                "url": doc or page,
                "image": thumb,
                "associatedMedia": media,
                "about": {"@id": self.org_id},
            })
        return nodes

    # -- 01 Home --------------------------------------------------------

    def home(self):
        site = self.site
        address = self.address()
        types = self.org_types()
        logo = self.logo_url()

        founder = None
        ci = self.contact
        if _s(ci.get("ceo_name")):
            founder = {
                "@type": "Person",
                "name": _s(ci.get("ceo_name")),
                "jobTitle": _s(ci.get("ceo_designation")),
                "worksFor": {"@id": self.org_id},
            }

        org = {
            "@type": types,
            "@id": self.org_id,
            "name": self.name,
            "legalName": _s(getattr(site, "legal_name", "")) if _s(getattr(site, "legal_name", "")) != self.name else "",
            "url": self.root,
            "logo": {"@id": self.logo_id} if logo else None,
            "image": {"@id": self.logo_id} if logo else None,
            "description": self.brand_description(),
            "email": self.email(),
            "telephone": self.telephone(),
            "address": address,
            "sameAs": self.same_as(),
            "areaServed": {"@type": "Country", "name": _COUNTRY_NAMES.get(self.country, self.country)},
            "contactPoint": self.contact_points(),
            "parentOrganization": self.parent_ref(),
        }
        # The contact person is the founder only when that is their designation.
        if founder:
            org["founder" if "founder" in _s(ci.get("ceo_designation")).lower() else "employee"] = founder
        if self.b2b:
            org["hasCertification"] = self.certification_nodes()

        # Sitelinks search box: the catalogue's own title search (?q=).
        search = None
        shop = _abs_route(self.request, "public_all_products")
        if shop:
            search = {
                "@type": "SearchAction",
                "target": {"@type": "EntryPoint", "urlTemplate": f"{shop}?q={{search_term_string}}"},
                "query-input": "required name=search_term_string",
            }

        nodes = [
            org,
            self.operator(),
            {
                "@type": "WebSite",
                "@id": self.website_id,
                "url": self.root,
                "name": self.name,
                "publisher": {"@id": self.org_id},
                "potentialAction": search,
                "inLanguage": self.lang,
            },
        ]
        if logo:
            nodes.append({
                "@type": "ImageObject",
                "@id": self.logo_id,
                "url": logo,
                "contentUrl": logo,
                "caption": _first(self.c.get("desktop_logo_alt"), self.name),
            })
        nodes.append({
            "@type": "WebPage",
            "@id": self.page_id,
            "url": self.page_url,
            "name": _first(self.seo.get("meta_title"), self.name),
            "isPartOf": {"@id": self.website_id},
            "about": {"@id": self.org_id},
            "mainEntity": {"@id": self.org_id},
            "inLanguage": self.lang,
        })
        # Only the products the home page actually shows (its best-sellers /
        # latest strip), never the whole catalogue.
        featured = self.item_list(
            f"{self.page_url}#featured-products",
            _first(self.c.get("top_seller_title"), "Featured products"),
            self.product_entries(self.ctx.get("latest_products")),
        )
        nodes.append(featured)
        return nodes

    # -- 02 Contact / 03 About -------------------------------------------

    def contact_page(self):
        return [
            self.page("ContactPage", name=f"Contact {self.name}",
                      about={"@id": self.org_id}, main_entity={"@id": self.org_id}),
            self.breadcrumbs([("Contact", self.page_url)]),
        ]

    def about_page(self):
        nodes = [
            self.page("AboutPage", name=f"About {self.name}",
                      about={"@id": self.org_id}, main_entity={"@id": self.org_id}),
            self.breadcrumbs([("About Us", self.page_url)]),
        ]
        if self.parent_name:
            # The About page states the brand/company relationship, so it
            # carries it too: the store (same @id as on the home page, so the
            # nodes merge) owned by the operator organization.
            nodes += [
                {
                    "@type": self.org_types(),
                    "@id": self.org_id,
                    "name": self.name,
                    "url": self.root,
                    "parentOrganization": self.parent_ref(),
                },
                self.operator(),
            ]
        return nodes

    # -- 04 Blog listing -------------------------------------------------

    def blog_list(self):
        entries = []
        for b in self.ctx.get("blogs") or []:
            d = b if isinstance(b, dict) else getattr(b, "_d", {})
            slug = _s(d.get("slug")) if isinstance(d, dict) else ""
            if slug:
                entries.append((_abs_route(self.request, "public_blog_detail", blog_slug=slug), _s(d.get("title"))))
        lst = self.item_list(f"{self.page_url}#blog-list", f"{self.name} Blog Articles", entries)
        return [
            self.page("CollectionPage", name=f"{self.name} Blog",
                      description=_first(self.seo.get("meta_description"), _plain(self.c.get("blog_subtitle"), 300)),
                      main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("Blogs", self.page_url)]),
        ]

    # -- 05 Blog post ----------------------------------------------------

    def blog_post(self):
        blog = self.ctx.get("blog")
        d = getattr(blog, "_d", None)
        if not isinstance(d, dict):
            return None
        title = _s(d.get("title"))
        date = getattr(blog, "created_at", None)
        author = _s(d.get("author"))
        image = _abs(self.request, d.get("image"))
        post = {
            "@type": "BlogPosting",
            "@id": f"{self.page_url}#article",
            "url": self.page_url,
            "headline": title[:110],
            "description": _first(_plain(d.get("excerpt"), 300), self.seo.get("meta_description")),
            "image": [image] if image else [],
            # The post's own date. The store keeps no separate edit date, so the
            # last meaningful modification is the publication itself.
            "datePublished": date.isoformat() if date else "",
            "dateModified": date.isoformat() if date else "",
            # A named author when the post has one; otherwise the store, which
            # is genuinely its publisher. Never an invented person.
            "author": {"@type": "Person", "name": author} if author else {"@id": self.org_id},
            "publisher": {"@id": self.org_id},
            "mainEntityOfPage": {"@type": "WebPage", "@id": self.page_id},
            "inLanguage": self.lang,
        }
        return [post, self.breadcrumbs([("Blogs", _abs_route(self.request, "public_blogs")), (title, self.page_url)])]

    # -- 06 All products / 07 All categories / 08 Category ----------------

    def all_products(self):
        lst = self.item_list(f"{self.page_url}#products", f"{self.name} Products",
                             self.product_entries(self.ctx.get("products")))
        return [
            self.page("CollectionPage", name=f"All Products | {self.name}",
                      main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("All Products", self.page_url)]),
        ]

    def all_categories(self):
        entries = []
        for cat in self.ctx.get("categories") or []:
            slug = _s(getattr(cat, "slug", ""))
            if slug:
                entries.append((_abs_route(self.request, "public_category_detail", category_slug=slug), _s(cat.name)))
        lst = self.item_list(f"{self.page_url}#categories", f"{self.name} Categories", entries)
        return [
            self.page("CollectionPage", name=f"All Categories | {self.name}",
                      main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("Categories", self.page_url)]),
        ]

    def category(self):
        cat = self.ctx.get("category")
        if cat is None:
            return None
        lst = self.item_list(f"{self.page_url}#products", f"{cat.name} Products",
                             self.product_entries(self.ctx.get("products")))
        return [
            self.page("CollectionPage", name=f"{cat.name} | {self.name}",
                      # Only the category's own description; none is invented.
                      description=_plain(cat.description, 500),
                      main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("Categories", _abs_route(self.request, "public_categories")), (cat.name, self.page_url)]),
        ]

    # -- 09 Product ------------------------------------------------------

    def _return_policy(self, product):
        """The product's own return policy, exactly as its page states it
        (``Product.return_policy_label`` reads the same fields):

        * non-returnable (the field, or a "Non returnable" trust badge) →
          ``MerchantReturnNotPermitted`` with ``merchantReturnDays`` 0;
        * returnable / replacement-only → a finite window of the product's own
          ``return_days`` (replacement adds ``ExchangeRefund``);
        * no policy chosen, or a returnable product saved without its window →
          nothing (no claim the page doesn't make)."""
        rt = _s(getattr(product, "effective_return_type", product.return_type))
        if rt == "non_returnable":
            return {
                "@type": "MerchantReturnPolicy",
                "applicableCountry": self.country,
                "returnPolicyCategory": f"{SCHEMA}/MerchantReturnNotPermitted",
                "merchantReturnDays": 0,
            }
        days = getattr(product, "effective_return_days", None)
        if rt in ("returnable", "replacement") and days:
            node = {
                "@type": "MerchantReturnPolicy",
                "applicableCountry": self.country,
                "returnPolicyCategory": f"{SCHEMA}/MerchantReturnFiniteReturnWindow",
                "merchantReturnDays": days,
                "merchantReturnLink": _abs_route(self.request, "public_refund"),
            }
            if rt == "replacement":
                node["refundType"] = f"{SCHEMA}/ExchangeRefund"
            return node
        return None

    def _variants(self, product, images, brand_node, make_offer):
        """``(hasVariant nodes, variesBy)`` from the product's stored variation
        combinations, or ``([], [])`` when it has fewer than two."""
        from commerce.cart import _variant_price, attrs_of, combos_of

        combos = [c for c in combos_of(product) if attrs_of(c)]
        if len(combos) < 2:
            return [], []
        title = _s(product.title)
        varies, nodes = [], []
        for i, combo in enumerate(combos[:_LIST_LIMIT], start=1):
            attrs = [a for a in attrs_of(combo) if a["name"] and a["value"]]
            label = ", ".join(f"{a['name']}: {a['value']}" for a in attrs)
            node = {
                "@type": "Product",
                "@id": f"{self.page_url}#variant-{i}",
                "name": f"{title} ({label})" if label else title,
                "sku": _s(combo.get("sku")),
                "mpn": _s(combo.get("sku")),
                "image": [u for u in (_abs(self.request, x) for x in (combo.get("images") or [])) if u] or images,
                "brand": brand_node,
            }
            extra = []
            for a in attrs:
                prop = _VARIES_BY.get(a["name"].strip().lower())
                if prop:
                    node[prop] = a["value"]
                    if f"{SCHEMA}/{prop}" not in varies:
                        varies.append(f"{SCHEMA}/{prop}")
                else:
                    extra.append({"@type": "PropertyValue", "name": a["name"], "value": a["value"]})
            node["additionalProperty"] = extra
            try:
                stock = int(combo.get("inventory") or 0)
            except (TypeError, ValueError):
                stock = 0
            node["offers"] = make_offer(_variant_price(product, combo), stock <= 0)
            nodes.append(node)
        return nodes, varies

    def product(self):
        proxy = self.ctx.get("product")
        product = getattr(proxy, "_p", None)
        if product is None:
            return None
        req = self.request

        # Real uploaded photos only, never the demo fallback the page shows
        # for an imageless product.
        images = []
        for img in getattr(proxy, "image_objects", None) or []:
            u = _file_url(req, getattr(img, "image", None))
            if u and u not in images:
                images.append(u)

        specs = {
            _s(s.get("name")).lower(): _s(s.get("value"))
            for s in (product.specifications or []) if isinstance(s, dict) and _s(s.get("value"))
        }

        def spec(keys):
            return next((specs[k] for k in keys if specs.get(k)), "")

        gtin = re.sub(r"\D", "", spec(_SPEC_GTIN))
        if len(gtin) not in (8, 12, 13, 14):
            gtin = ""
        # The brand the owner set on the product (e.g. a brand the store
        # resells), else a "Brand" specification row; otherwise a D2C store
        # sells its own brand, while a B2B catalogue names none.
        brand = _s(getattr(product, "brand", "")) or spec(_SPEC_BRAND) or ("" if self.b2b else self.name)
        sku = _s(product.sku)
        # The manufacturer part number: a stated MPN, else the product's own
        # model code (its SKU).
        mpn = spec(_SPEC_MPN) or sku

        details = product.d2c_details if isinstance(product.d2c_details, dict) else {}
        description = _plain(product.description, _DESC_LIMIT) or "; ".join(
            _s(b) for b in (details.get("bullets") or []) if _s(b)
        )

        cat = product.category if product.category_id else None
        if cat is not None and (not getattr(cat, "is_active", True) or getattr(cat, "is_deleted", False)):
            cat = None

        currency = _s(product.currency) or self.currency
        return_policy = self._return_policy(product)

        def make_offer(price_value, out_of_stock):
            price = _money(price_value)
            if not price:
                return None
            offer = {
                "@type": "Offer",
                "url": self.page_url,
                "priceCurrency": currency,
                "price": price,
                "availability": f"{SCHEMA}/OutOfStock" if out_of_stock else f"{SCHEMA}/InStock",
                "itemCondition": f"{SCHEMA}/NewCondition",
                "hasMerchantReturnPolicy": return_policy,
            }
            if self.b2b:
                # A trade offer: business buyers only, from the minimum order quantity.
                moq = max(int(getattr(proxy, "moq", 1) or 1), 1)
                offer.update({
                    "businessFunction": "http://purl.org/goodrelations/v1#Sell",
                    "eligibleCustomerType": "http://purl.org/goodrelations/v1#Business",
                    "eligibleQuantity": {"@type": "QuantitativeValue", "minValue": moq, "unitCode": "C62"},
                })
            return offer

        brand_node = {"@type": "Brand", "name": brand} if brand else None
        variants, varies_by = self._variants(product, images, brand_node, make_offer)

        node = {
            "@type": "Product",
            "@id": f"{self.page_url}#product",
            "url": self.page_url,
            "name": _s(product.title),
            "image": images,
            "description": description,
            "sku": sku,
            "gtin": gtin,
            "mpn": mpn,
            "brand": brand_node,
            "category": _s(cat.name) if cat is not None else "",
            "offers": make_offer(product.price, product.is_out_of_stock),
        }
        if variants:
            # One page, several purchasable combinations (e.g. pack sizes): a
            # ProductGroup whose variants each carry their own SKU, price and
            # stock. The group itself is not sold, so it has no offer.
            node.update({
                "@type": "ProductGroup",
                "productGroupID": sku or str(product.pk),
                "variesBy": varies_by,
                "hasVariant": variants,
                # productGroupID carries the parent code; identifiers belong
                # to the variants.
                "offers": None,
                "sku": "",
                "gtin": "",
                "mpn": "",
            })

        # Genuine ratings and reviews only: the approved reviews the page shows.
        total = int(self.ctx.get("total_reviews") or 0)
        avg = self.ctx.get("avg_rating") or 0
        if total > 0 and avg:
            node["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": avg, "reviewCount": total}
            reviews = []
            for r in list(self.ctx.get("reviews") or [])[:_REVIEW_LIMIT]:
                author, rating = _s(getattr(r, "name", "")), getattr(r, "rating", None)
                if author and rating:
                    reviews.append({
                        "@type": "Review",
                        "author": {"@type": "Person", "name": author},
                        "reviewRating": {"@type": "Rating", "ratingValue": int(rating), "bestRating": 5},
                        "reviewBody": _plain(getattr(r, "comment", ""), 2000),
                    })
            node["review"] = reviews

        # Mirrors the page's breadcrumb: Home / Category / Product.
        trail = []
        if cat is not None:
            trail.append((cat.name, _abs_route(req, "public_category_detail", category_slug=cat.slug)))
        trail.append((product.title, self.page_url))
        return [node, self.breadcrumbs(trail)]

    # -- pages beyond the specification, in the same style ---------------

    def certificates(self):
        if not self.site.is_b2b:
            return None
        certs = self.certification_nodes()
        lst = None
        if certs:
            lst = {
                "@type": "ItemList",
                "@id": f"{self.page_url}#certificates",
                "name": f"{self.name} Certificates",
                "itemListOrder": f"{SCHEMA}/ItemListUnordered",
                "numberOfItems": len(certs),
                "itemListElement": [
                    {"@type": "ListItem", "position": i, "item": n} for i, n in enumerate(certs, start=1)
                ],
            }
        return [
            self.page("CollectionPage", name=f"Certificates | {self.name}",
                      description=self.seo.get("meta_description"),
                      about={"@id": self.org_id}, main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("Certificates", self.page_url)]),
        ]

    def policy(self, key):
        label = {
            "privacy": "Privacy Policy", "terms": "Terms & Conditions",
            "refund": "Refund Policy", "shipping": "Shipping Policy",
        }[key]
        return [
            self.page("WebPage", name=f"{label} | {self.name}",
                      description=self.seo.get("meta_description"), about={"@id": self.org_id}),
            self.breadcrumbs([(label, self.page_url)]),
        ]

    def custom_policy(self, key):
        """An owner's extra policy page (storefront.policies), at /policies/<slug>/."""
        from .policies import by_number

        policy = by_number(self.c, key.split(":", 1)[1])
        if policy is None:
            return None
        label = policy["title"]
        return [
            self.page("WebPage", name=f"{label} | {self.name}",
                      description=self.seo.get("meta_description"), about={"@id": self.org_id}),
            self.breadcrumbs([(label, self.page_url)]),
        ]

    def core_index(self):
        entries = [
            (_abs_route(self.request, "public_core_page", page_slug=p["slug"]), _s(p.get("title")))
            for p in (self.ctx.get("pages") or []) if isinstance(p, dict) and _s(p.get("slug"))
        ]
        lst = self.item_list(f"{self.page_url}#pages", f"{self.name} Pages", entries)
        return [
            self.page("CollectionPage", name=f"Pages | {self.name}",
                      main_entity={"@id": lst["@id"]} if lst else None),
            lst,
            self.breadcrumbs([("Pages", self.page_url)]),
        ]

    def core(self, key):
        path = key.split(":", 1)[1].split("/")
        tree = {k: node for k, _label, node in _walk_core(self.c.get("core_pages"))}
        node = tree.get(key)
        if node is None:
            return None
        url_names = ("public_core_page", "public_core_subpage", "public_core_child")
        kw_names = ("page_slug", "sub_slug", "child_slug")
        trail = [("Pages", _abs_route(self.request, "public_core_pages"))]
        for depth in range(len(path)):
            n = tree.get("core:" + "/".join(path[: depth + 1])) or {}
            url = _abs_route(self.request, url_names[depth], **{kw_names[i]: path[i] for i in range(depth + 1)})
            trail.append((_first(n.get("title"), path[depth]), url))
        return [
            self.page("WebPage", name=_first(self.seo.get("meta_title"), node.get("title")),
                      description=_first(_plain(node.get("subtitle"), 300), self.seo.get("meta_description")),
                      about={"@id": self.org_id}),
            self.breadcrumbs(trail),
        ]

    # -- dispatch -------------------------------------------------------

    def build(self, key: str):
        kind = key.split(":", 1)[0]
        if key == "home":
            nodes = self.home()
        elif key == "contact":
            nodes = self.contact_page()
        elif key == "about":
            nodes = self.about_page()
        elif key == "blogs":
            nodes = self.blog_list()
        elif kind == "blog":
            nodes = self.blog_post()
        elif key == "all_products":
            nodes = self.all_products()
        elif key == "categories":
            nodes = self.all_categories()
        elif kind == "category":
            nodes = self.category()
        elif kind == "product":
            nodes = self.product()
        elif key == "certificates":
            nodes = self.certificates()
        elif key in ("privacy", "terms", "refund", "shipping"):
            nodes = self.policy(key)
        elif kind == "policy":
            nodes = self.custom_policy(key)
        elif key == "core_pages":
            nodes = self.core_index()
        elif kind == "core":
            nodes = self.core(key)
        else:
            nodes = None
        if not nodes:
            return None
        graph = [n for n in (_prune(x) for x in nodes if x) if n]
        return {"@context": SCHEMA, "@graph": graph} if graph else None


def page_schema_json(request, site, content, seo, ctx) -> str:
    """The JSON-LD for this storefront page, ready to print inside
    ``<script type="application/ld+json">`` (or ``""`` for pages that carry no
    structured data of their own: cart, checkout, account …)."""
    try:
        key = (seo or {}).get("page_key") or ""
        if not key:
            return ""
        graph = _Graph(request, site, content, seo, ctx).build(key)
        return _json(graph) if graph else ""
    except Exception:  # structured data must never take a page down
        logger.exception("storefront structured data failed for %s", getattr(request, "path", "?"))
        return ""
