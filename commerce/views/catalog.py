"""Catalogue management: products, categories, inventory, stock and reviews."""

from __future__ import annotations

import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from commerce import activity, field_tips, trust_badges, trust_icons, variant_profiles
from commerce.context import get_owned_site, store_context
from commerce.forms import CategoryForm, ProductForm
from commerce.media_processing import clone_asset_file
from commerce.models import (
    Category,
    MediaAsset,
    Product,
    ProductImage,
    Review,
    display_ordered_categories,
)

# Homepage Best Sellers section is capped, exactly like the reference.
BEST_SELLERS_MAX = 8

# Storefront presentation toggles stored on ``site.content``. Each maps a URL
# flag slug → the content key it flips, so one view serves all four switches.
PRODUCT_FLAGS = {
    "hover_swap": "enable_product_hover_swap",
    "detail_zoom": "enable_product_detail_zoom",
    "show_price": "show_price_in_lists",
    "abandoned_cart": "abandoned_cart_emails_enabled",
}


def _paginate(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page") or 1)


def _content_flag(site, key, default=False) -> bool:
    try:
        return bool((site.content or {}).get(key, default))
    except Exception:
        return default


#: Product gallery cap  the six image blocks on the Images step.
PRODUCT_IMAGES_MAX = 6


def _save_product_images(request, site, product) -> None:
    """Apply the product-image changes from the form.

    * ``remove_image``  ids of existing ProductImages the owner deleted.
    * ``media_image``   ids of library assets picked; each file is copied into
      a new ProductImage (owned by the product, so deleting the library asset
      never blanks it).
    * ``image1..image6``  legacy direct file uploads, still honoured.
    * ``image_slot``  ``"<image id>:<block>"`` for every saved image, and
      ``media_image_slot``  the block of each ``media_image`` (same order).

    Each image keeps the block (1-6) it sits in on the Images step, stored as
    its ``order``: removing the fourth image leaves block four empty instead of
    pulling the fifth up into it, and the edit page puts every image back in
    its own block. The storefront simply shows them by ``order``, so a gap just
    closes up there and the first image present is the main one. An image
    without a (free) block takes the first empty one. A product holds at most
    ``PRODUCT_IMAGES_MAX`` images; extras in a tampered POST are ignored.
    """
    scoped = ProductImage.objects.for_site(site)

    def _block(raw):
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return n if 1 <= n <= PRODUCT_IMAGES_MAX else None

    remove_ids = [r for r in request.POST.getlist("remove_image") if r]
    if remove_ids:
        scoped.filter(product=product, pk__in=remove_ids).delete()

    # Saved images move to the block they now sit in on the form.
    blocks = {}
    for raw in request.POST.getlist("image_slot"):
        image_id, _, pos = str(raw).partition(":")
        n = _block(pos)
        if image_id and n:
            blocks[image_id.strip()] = n
    current = list(scoped.filter(product=product))
    for img in current:
        n = blocks.get(str(img.pk))
        if n and img.order != n:
            img.order = n
            img.save(update_fields=["order", "updated_at"])
    taken = {img.order for img in current}
    room = PRODUCT_IMAGES_MAX - len(current)

    def _place(wanted) -> int:
        """The block for a new image: the one asked for when it is free, else
        the first empty one."""
        n = _block(wanted)
        if n is None or n in taken:
            n = next((k for k in range(1, PRODUCT_IMAGES_MAX + 1) if k not in taken), None)
            if n is None:
                n = max(taken, default=0) + 1
        taken.add(n)
        return n

    media_blocks = request.POST.getlist("media_image_slot")
    for idx, asset_id in enumerate(request.POST.getlist("media_image")):
        if room <= 0:
            break
        asset = (
            MediaAsset.objects.for_site(site)
            .filter(pk=asset_id, kind=MediaAsset.Kind.IMAGE)
            .first()
        )
        if asset is None:
            continue
        try:
            cloned = clone_asset_file(asset, f"products/{product.id}")
        except Exception:
            continue
        room -= 1
        ProductImage.objects.create(
            site=site, product=product, image=cloned, alt_text=asset.alt_text,
            order=_place(media_blocks[idx] if idx < len(media_blocks) else None),
        )

    for i in range(1, PRODUCT_IMAGES_MAX + 1):
        upload = request.FILES.get(f"image{i}")
        if upload and room > 0:
            room -= 1
            ProductImage.objects.create(site=site, product=product, image=upload, order=_place(i))


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------
@login_required
def products(request, site_id):
    site = get_owned_site(request, site_id)
    # The reference loads the full list and filters client-side (instant search
    # + category dropdown), so we hand the template every product.
    products_all = list(
        Product.objects.for_site(site)
        .filter(is_deleted=False)
        .select_related("category")
        .prefetch_related("categories", "images")
    )
    categories_all = Category.objects.for_site(site).filter(is_deleted=False)

    # Best Sellers, ordered by the saved order then recency; capped at 8.
    saved_order = (site.content or {}).get("best_sellers_order") or []
    order_index = {str(pid): i for i, pid in enumerate(saved_order)}
    best_sellers = [p for p in products_all if p.is_best_seller]
    best_sellers.sort(key=lambda p: order_index.get(str(p.id), len(order_index)))
    best_sellers = best_sellers[:BEST_SELLERS_MAX]

    # Storefront flags, read once: they feed both the switches and the
    # "N of M on" summary on the Display settings section tab.
    hover_swap = _content_flag(site, "enable_product_hover_swap")
    detail_zoom = _content_flag(site, "enable_product_detail_zoom")
    show_price = _content_flag(site, "show_price_in_lists", default=True)
    abandoned_cart = _content_flag(site, "abandoned_cart_emails_enabled")
    flags = [hover_swap, detail_zoom] + ([show_price, abandoned_cart] if site.is_d2c else [])
    active_count = sum(1 for p in products_all if p.is_active)

    context = store_context(request, site, "products")
    context.update(
        {
            "products_all": products_all,
            "categories_all": categories_all,
            "products_active_count": active_count,
            "products_draft_count": len(products_all) - active_count,
            "best_sellers_starred_count": sum(1 for p in products_all if p.is_best_seller),
            "best_sellers_max": BEST_SELLERS_MAX,
            "display_flags_on": sum(1 for f in flags if f),
            "display_flags_total": len(flags),
            "best_sellers_products": best_sellers,
            "best_sellers_order_json": json.dumps([str(p.id) for p in best_sellers]),
            "best_sellers_default_order_json": json.dumps([str(p.id) for p in best_sellers]),
            "best_sellers_subtitle": (site.content or {}).get("best_sellers_subtitle", ""),
            "enable_product_hover_swap": hover_swap,
            "enable_product_detail_zoom": detail_zoom,
            "show_price_in_lists": show_price,
            "abandoned_cart_emails_enabled": abandoned_cart,
            "enquiry_form_mode": (site.content or {}).get("enquiry_form_mode", "mobile_qty"),
            "currency": site.currency,
        }
    )
    return render(request, "store/products.html", context)


# --------------------------------------------------------------------------
# Storefront presentation toggles + Best Sellers curation (AJAX, JSON out)
# --------------------------------------------------------------------------
@login_required
@require_POST
def toggle_product_flag(request, site_id, flag):
    """Flip one storefront presentation flag stored on ``site.content``."""
    site = get_owned_site(request, site_id)
    key = PRODUCT_FLAGS.get(flag)
    if not key:
        return JsonResponse({"success": False, "error": "Unknown flag."}, status=400)
    content = site.content or {}
    enabled = str(request.POST.get("enabled")).strip().lower() in ("1", "true", "yes", "on")
    content[key] = enabled
    site.content = content
    site.save(update_fields=["content"])
    return JsonResponse({"success": True, "enabled": enabled})


@login_required
@require_POST
def toggle_best_seller(request, site_id, product_id):
    """Mark/unmark a product as a Best Seller (capped at 8)."""
    site = get_owned_site(request, site_id)
    product = get_object_or_404(Product.objects.for_site(site).filter(is_deleted=False), pk=product_id)
    if not product.is_best_seller:
        current = Product.objects.for_site(site).filter(is_best_seller=True, is_deleted=False).count()
        if current >= BEST_SELLERS_MAX:
            return JsonResponse(
                {"success": False, "error": f"You can feature at most {BEST_SELLERS_MAX} best sellers."},
                status=400,
            )
    product.is_best_seller = not product.is_best_seller
    product.save(update_fields=["is_best_seller", "updated_at"])
    return JsonResponse({"success": True, "is_best_seller": product.is_best_seller})


@login_required
@require_POST
def update_best_sellers_order(request, site_id):
    """Persist the drag-ordered Best Sellers list (list of product ids)."""
    site = get_owned_site(request, site_id)
    try:
        order = json.loads(request.body.decode("utf-8") or "{}").get("order") or []
    except (ValueError, UnicodeDecodeError):
        order = request.POST.getlist("order")
    # Keep only ids that belong to this store and are best sellers.
    valid = set(
        str(pid)
        for pid in Product.objects.for_site(site)
        .filter(is_best_seller=True, is_deleted=False)
        .values_list("id", flat=True)
    )
    cleaned = [str(pid) for pid in order if str(pid) in valid]
    content = site.content or {}
    content["best_sellers_order"] = cleaned
    site.content = content
    site.save(update_fields=["content"])
    return JsonResponse({"success": True, "order": cleaned})


ENQUIRY_FORM_MODES = ("mobile_only", "mobile_qty", "email_qty", "all")


@login_required
@require_POST
def update_enquiry_form_mode(request, site_id):
    """Persist which fields the B2B product-enquiry form asks for."""
    site = get_owned_site(request, site_id)
    mode = (request.POST.get("mode") or "").strip()
    if mode not in ENQUIRY_FORM_MODES:
        return JsonResponse({"success": False, "error": "Unknown mode."}, status=400)
    content = site.content or {}
    content["enquiry_form_mode"] = mode
    site.content = content
    site.save(update_fields=["content"])
    return JsonResponse({"success": True, "mode": mode})


@login_required
@require_POST
def update_best_sellers_subtitle(request, site_id):
    site = get_owned_site(request, site_id)
    subtitle = (request.POST.get("subtitle") or "").strip()[:300]
    content = site.content or {}
    content["best_sellers_subtitle"] = subtitle
    site.content = content
    site.save(update_fields=["content"])
    return JsonResponse({"success": True, "subtitle": subtitle})


@login_required
@require_POST
def product_duplicate(request, site_id, product_id):
    """Clone a product (fields, categories, images) as a new draft, then open it."""
    site = get_owned_site(request, site_id)
    original = get_object_or_404(Product.objects.for_site(site).filter(is_deleted=False), pk=product_id)

    clone = Product.objects.for_site(site).get(pk=original.pk)
    clone.pk = None
    clone.id = None
    clone._state.adding = True
    clone.slug = ""  # regenerated uniquely on save
    clone.title = f"{original.title} (copy)"
    clone.is_best_seller = False
    clone.is_featured = False
    # SKUs are unique across the store, so the copy (and each of its
    # variations) gets its own: "TSHIRT-01" → "TSHIRT-01-COPY".
    taken = _store_skus(site)
    if (clone.sku or "").strip():
        clone.sku = _unique_sku(f"{clone.sku.strip()}-COPY", taken)
    combos = []
    for combo in clone.variant_combinations or []:
        if isinstance(combo, dict) and str(combo.get("sku") or "").strip():
            combo = dict(combo, sku=_unique_sku(f"{str(combo['sku']).strip()}-COPY", taken))
        combos.append(combo)
    if clone.variant_combinations:
        clone.variant_combinations = combos
    clone.save()
    # M2M + images copied after the row exists.
    clone.categories.set(original.categories.all())
    # Reuse the same stored files  a duplicate shares imagery until edited.
    for img in original.images.for_site(site):
        ProductImage.objects.create(
            site=site,
            product=clone,
            image=img.image.name,
            alt_text=img.alt_text,
            order=img.order,
        )
    activity.record(
        "store.product.duplicated", request=request, site=site,
        target=clone, target_kind="Product", label=clone.title,
    )
    messages.success(request, f"Duplicated “{original.title}”. Edit the copy below.")
    return redirect("store:product_edit", site_id=site.id, product_id=clone.id)


def _split_csv(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_zero(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


#: Per-variation gallery cap  the five image slots on the Variations step.
VARIANT_IMAGES_MAX = 5
_HEX_COLOR_RE = re.compile(r"#[0-9a-f]{6}")


def _parse_variant_combinations(post):
    """Read the hidden ``variant_combinations`` JSON the Variations step builds.

    Each row is normalised to the shape the rest of OneWooz already expects 
    ``{key, attributes:[{name,value}], sku, mrp, price, inventory}`` (see the
    storefront variant picker, the inventory table and product restock). Returns
    a list, or ``None`` when the product has no variants.
    """
    raw = (post.get("variant_combinations") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        return None

    out = []
    default_taken = False
    for combo in data:
        if not isinstance(combo, dict):
            continue
        attrs = []
        for a in combo.get("attributes") or []:
            if isinstance(a, dict) and (a.get("name") or a.get("value")):
                attrs.append(
                    {"name": str(a.get("name") or "").strip(), "value": str(a.get("value") or "").strip()}
                )
        if not attrs:
            continue
        key = str(combo.get("key") or "").strip() or "__".join(
            f"{a['name']}-{a['value']}" for a in attrs
        )
        # Per-combination gallery images (URLs picked from the media library). The
        # storefront swaps the product gallery to these when this variation is
        # selected. Kept as clean, de-duplicated strings.
        images = []
        for u in combo.get("images") or []:
            u = str(u or "").strip()
            if u and u not in images:
                images.append(u)
        images = images[:VARIANT_IMAGES_MAX]
        # Owner-picked swatch colour for a Color/Colour variation: the dot shown
        # beside that colour on the storefront product page. Only a strict
        # ``#rrggbb`` is kept  it is written into a style attribute there.
        swatch = str(combo.get("swatch") or "").strip().lower()
        if not _HEX_COLOR_RE.fullmatch(swatch):
            swatch = ""
        # "Default selected": the one variation pre-selected on the storefront
        # product page. At most one combination may hold it  the first ticked
        # wins, so a tampered POST can never send several defaults. When none is
        # ticked, the shopper must choose a variation before adding to cart.
        is_default = bool(combo.get("default_selected")) and not default_taken
        if is_default:
            default_taken = True
        row = {
            "key": key,
            "attributes": attrs,
            "sku": str(combo.get("sku") or "").strip(),
            "mrp": _num_or_none(combo.get("mrp")),
            "price": _num_or_none(combo.get("price")),
            "inventory": _int_or_zero(combo.get("inventory")),
            "default_selected": is_default,
            "images": images,
            "swatch": swatch,
        }
        # The variation's own product details ("Edit this variation" in the
        # form): only the fields that differ from the product, whitelisted and
        # normalised (commerce.variant_profiles). Absent = inherit everything.
        profile = variant_profiles.clean_profile(combo.get("profile"))
        if profile:
            row["profile"] = profile
        out.append(row)
    return out or None


_SKU_PART_RE = re.compile(r"[^A-Z0-9]+")


def _sku_part(value) -> str:
    """One variation value as an SKU fragment: "Navy Blue" → "NAVYBLUE"."""
    return _SKU_PART_RE.sub("", str(value or "").upper())[:12]


def suggested_variant_sku(base: str, attrs) -> str:
    """The SKU a variation gets when the owner leaves it blank: the product SKU
    plus each variation value ("TSHIRT-01" + Black / M → "TSHIRT-01-BLACK-M").
    Mirrors ``suggestSku`` in product_form.html, which shows it as the
    placeholder."""
    base = (base or "").strip()
    if not base:
        return ""
    parts = [p for p in (_sku_part(a.get("value")) for a in attrs or []) if p]
    return "-".join([base] + parts)[:100]


def _store_skus(site) -> set:
    """Every SKU in use in the store (product + variation), lower-cased."""
    taken = set()
    for sku, vcs in Product.objects.for_site(site).filter(is_deleted=False).values_list(
        "sku", "variant_combinations"
    ):
        # With variations the product SKU is ignored (each variation has its own).
        if (sku or "").strip() and not vcs:
            taken.add(sku.strip().lower())
        for c in vcs or []:
            if isinstance(c, dict) and str(c.get("sku") or "").strip():
                taken.add(str(c["sku"]).strip().lower())
    return taken


def _unique_sku(candidate: str, taken: set) -> str:
    """``candidate``, or ``candidate-2``, ``-3``… whichever is free; recorded
    in ``taken`` so the next call can't reuse it."""
    candidate = candidate[:96]
    sku, n = candidate, 2
    while sku.lower() in taken:
        sku = f"{candidate}-{n}"
        n += 1
    taken.add(sku.lower())
    return sku


def _resolve_skus(site, instance, product_sku, combos):
    """Make every SKU on the product distinct, and distinct from the store's
    other products. Returns a list of error messages (empty = all unique).

    * a blank variation SKU is filled from the product SKU + its values, with a
      numeric suffix if that would collide;
    * an SKU the owner typed that repeats another variation of this product or
      any other product in the store is an error  SKUs identify stock and
      orders, so two things may never share one.
    Once a product has variations its own (Pricing step) SKU is ignored, so a
    variation may reuse it; it is then only a prefix for suggested SKUs.
    Comparison ignores case and surrounding spaces.
    """
    errors = []
    taken = {}  # normalised SKU → where it is used (for the message)
    others = (
        Product.objects.for_site(site)
        .filter(is_deleted=False)
        .exclude(pk=getattr(instance, "pk", None))
        .values_list("title", "sku", "variant_combinations")
    )
    for title, sku, vcs in others:
        if (sku or "").strip() and not vcs:
            taken.setdefault(sku.strip().lower(), f"“{title}”")
        for c in vcs or []:
            csku = str((c or {}).get("sku") or "").strip() if isinstance(c, dict) else ""
            if csku:
                taken.setdefault(csku.lower(), f"a variation of “{title}”")

    own = {}
    base = (product_sku or "").strip()
    if base and not combos:
        if base.lower() in taken:
            errors.append(f"SKU “{base}” is already used by {taken[base.lower()]}. Every SKU must be unique.")
        own[base.lower()] = "this product"

    def label(combo):
        return " / ".join(a.get("value", "") for a in combo.get("attributes") or []) or "a variation"

    # Typed SKUs first, so an auto-filled one never claims a value the owner chose.
    for combo in combos or []:
        sku = (combo.get("sku") or "").strip()
        if not sku:
            continue
        key = sku.lower()
        if key in own:
            errors.append(
                f"SKU “{sku}” on {label(combo)} is the same as {own[key]}. Give each variation its own SKU."
            )
        elif key in taken:
            errors.append(f"SKU “{sku}” on {label(combo)} is already used by {taken[key]}.")
        else:
            own[key] = f"variation {label(combo)}"
    for combo in combos or []:
        if (combo.get("sku") or "").strip():
            continue
        candidate = suggested_variant_sku(base, combo.get("attributes"))
        if not candidate:
            continue
        sku, n = candidate, 2
        while sku.lower() in own or sku.lower() in taken:
            sku = f"{candidate}-{n}"
            n += 1
        combo["sku"] = sku
        own[sku.lower()] = f"variation {label(combo)}"
    return errors


def _clean_list(values):
    return [v.strip() for v in values if v and v.strip()]


def _collect_json_fields(request):
    """Assemble the repeatable / list inputs from the multi-step form into the
    values stored on the product's JSON columns."""
    post = request.POST

    faqs = [
        {"question": q.strip(), "answer": a.strip()}
        for q, a in zip(post.getlist("faq_q"), post.getlist("faq_r"))
        if q.strip() and a.strip()
    ]
    specs = [
        {"name": k.strip(), "value": v.strip()}
        for k, v in zip(post.getlist("spec_k"), post.getlist("spec_v"))
        if k.strip()
    ]
    extras = [
        {"name": n.strip(), "value": v.strip()}
        for n, v in zip(post.getlist("extra_name"), post.getlist("extra_value"))
        if n.strip()
    ]
    variants = []
    for name, vals in zip(post.getlist("variant_type_name"), post.getlist("variant_type_values")):
        values = _split_csv(vals)
        if name.strip() and values:
            variants.append({"name": name.strip(), "values": values})

    d2c_details = {
        "bullets": _clean_list(post.getlist("bullets")),
        "features": _split_csv(post.get("features")),
        "accessories": _clean_list(post.getlist("accessories")),
        "includes": _clean_list(post.getlist("includes")),
        "extras": extras,
        "recommended_use": (post.get("recommended_use") or "").strip(),
        "material": (post.get("material") or "").strip(),
        "colour": (post.get("colour") or "").strip(),
        "handling_time": (post.get("handling_time") or "").strip(),
        "warning": (post.get("warning") or "").strip(),
        "support_phone": (post.get("support_phone") or "").strip(),
        "support_email": (post.get("support_email") or "").strip(),
    }
    return {
        "tags": _split_csv(request.POST.get("keywords")),
        "faqs": faqs,
        "specifications": specs,
        "trusted_icons": trust_badges.clean_selection(request.POST.getlist("trusted_icons")),
        "variants": variants or None,
        "variant_combinations": _parse_variant_combinations(post),
        "d2c_details": d2c_details,
    }


def _product_prefill(product):
    """Flatten a product's JSON columns back into the shapes the template's
    repeatable rows expect, so editing shows what was saved."""
    if product is None:
        return {
            "categories_multi": [],
            "has_variations": False,
            "variant_combinations_json": "[]",
            "is_best_seller": False,
        }
    details = product.d2c_details or {}
    return {
        "categories_multi": [str(c.id) for c in product.categories.all()],
        "has_variations": bool(product.variant_combinations or product.variants),
        "variant_combinations_json": json.dumps(product.variant_combinations or []),
        "is_best_seller": bool(product.is_best_seller),
        "bullets": details.get("bullets") or [],
        "features": ", ".join(details.get("features") or []),
        "keywords": ", ".join(product.tags or []),
        "accessories": details.get("accessories") or [],
        "includes": details.get("includes") or [],
        # Pair-shaped ({a, b}) rows for the two-input repeatable partial.
        "extras": [{"a": e.get("name", ""), "b": e.get("value", "")} for e in details.get("extras") or []],
        "faqs": [{"a": f.get("question", ""), "b": f.get("answer", "")} for f in product.faqs or []],
        "specs": [{"a": s.get("name", ""), "b": s.get("value", "")} for s in product.specifications or []],
        "variants": [{"a": v.get("name", ""), "b": ", ".join(v.get("values") or [])} for v in product.variants or []],
        "trusted_icons": product.trusted_icons or [],
        "recommended_use": details.get("recommended_use", ""),
        "material": details.get("material", ""),
        "colour": details.get("colour", ""),
        "handling_time": details.get("handling_time", ""),
        "warning": details.get("warning", ""),
        "support_phone": details.get("support_phone", ""),
        "support_email": details.get("support_email", ""),
    }


def _prefill_from_post(request, posted):
    """``_product_prefill`` built from a submitted (but invalid) form, so a
    validation error never throws away the owner's unsaved rows."""
    details = posted.get("d2c_details") or {}
    post = request.POST
    combos = posted.get("variant_combinations") or []
    return {
        "categories_multi": [c for c in post.getlist("categories_multi") if c],
        "has_variations": bool(combos or posted.get("variants")) or post.get("has_variations") == "yes",
        "variant_combinations_json": json.dumps(combos),
        "is_best_seller": bool(post.get("is_best_seller")),
        "bullets": details.get("bullets") or [],
        "features": ", ".join(details.get("features") or []),
        "keywords": ", ".join(posted.get("tags") or []),
        "accessories": details.get("accessories") or [],
        "includes": details.get("includes") or [],
        "extras": [{"a": e.get("name", ""), "b": e.get("value", "")} for e in details.get("extras") or []],
        "faqs": [{"a": f.get("question", ""), "b": f.get("answer", "")} for f in posted.get("faqs") or []],
        "specs": [{"a": s.get("name", ""), "b": s.get("value", "")} for s in posted.get("specifications") or []],
        "variants": [
            {"a": v.get("name", ""), "b": ", ".join(v.get("values") or [])}
            for v in posted.get("variants") or []
        ],
        "trusted_icons": posted.get("trusted_icons") or [],
        "recommended_use": details.get("recommended_use", ""),
        "material": details.get("material", ""),
        "colour": details.get("colour", ""),
        "handling_time": details.get("handling_time", ""),
        "warning": details.get("warning", ""),
        "support_phone": details.get("support_phone", ""),
        "support_email": details.get("support_email", ""),
    }


def _trusted_badge_options(site):
    """Badges offered in the product form's picker: the platform set (retired
    badges removed) followed by the store's own badges. See commerce.trust_badges."""
    return trust_badges.options(site)


@login_required
def product_form(request, site_id, product_id=None):
    site = get_owned_site(request, site_id)
    instance = None
    if product_id:
        instance = get_object_or_404(
            Product.objects.for_site(site).filter(is_deleted=False), pk=product_id
        )

    posted = None
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=instance, site=site)
        posted = _collect_json_fields(request)
        if form.is_valid():
            for msg in _resolve_skus(
                site, instance, form.cleaned_data.get("sku"), posted.get("variant_combinations")
            ):
                form.add_error(None, msg)
        if form.is_valid():
            product = form.save(commit=False)
            product.site = site
            for attr, value in posted.items():
                setattr(product, attr, value)
            # Best-seller flag isn't a ModelForm field; set it explicitly.
            product.is_best_seller = bool(request.POST.get("is_best_seller"))
            # A warranty trust badge *is* the product's warranty: it fills the
            # Compliance "Guarantee / warranty" field (locked in the form).
            warranty_badge = next(
                (n for n in (product.trusted_icons or []) if trust_badges.is_warranty(n)), None
            )
            if warranty_badge:
                product.guarantee_warranty = trust_badges.display_name(warranty_badge)
            # Each variation's own details: categories checked against this
            # store, its badges locking its return policy / warranty the way the
            # product's badges lock the product's (commerce.variant_profiles).
            variant_profiles.finalise_profiles(product, site)
            # A video picked from the library sets the product's video link.
            vid_asset_id = (request.POST.get("media_video") or "").strip()
            if vid_asset_id:
                asset = (
                    MediaAsset.objects.for_site(site)
                    .filter(pk=vid_asset_id, kind=MediaAsset.Kind.VIDEO)
                    .first()
                )
                if asset and asset.url:
                    product.video_link = asset.url
            # Variant products carry stock per combination, not on the product.
            if product.variant_combinations:
                product.inventory = None
            product.save()
            form.save_m2m()

            # Additional (secondary) categories, scoped to this store.
            # The main category is never also an "additional" one.
            cat_ids = [
                c
                for c in request.POST.getlist("categories_multi")
                if c and c != str(product.category_id or "")
            ]
            if cat_ids:
                product.categories.set(
                    Category.objects.for_site(site).filter(pk__in=cat_ids, is_deleted=False)
                )
            else:
                product.categories.clear()

            _save_product_images(request, site, product)
            # Images and categories land after product.save(); queue the Google
            # Merchant sync again so the listing gets them (deduplicated by the
            # payload hash, so an unchanged product isn't re-sent).
            from commerce import google_merchant

            google_merchant.queue_product_sync(product)
            activity.record(
                "store.product.updated" if instance else "store.product.created",
                request=request, site=site, target=product,
                target_kind="Product", label=product.title,
            )
            messages.success(request, f"“{product.title}” saved.")
            return redirect("store:products", site_id=site.id)
    else:
        form = ProductForm(instance=instance, site=site)

    context = store_context(request, site, "products")
    context.update(
        {
            "form": form,
            "product": instance,
            "is_edit": bool(instance),
            "is_b2b": site.business_model == site.BusinessModel.B2B,
            "is_d2c": site.is_d2c,
            "trusted_badges": _trusted_badge_options(site),
            "trust_icon_catalog": trust_icons.catalog(),
            "trust_icon_svgs": trust_icons.ICON_MAP,
            # After a failed save, re-show what the owner typed (variations,
            # bullets, FAQs…) rather than the last saved copy.
            "prefill": _prefill_from_post(request, posted) if posted is not None else _product_prefill(instance),
            "categories": Category.objects.for_site(site).filter(is_deleted=False),
            "existing_images": instance.images.for_site(site) if instance else [],
            # {field key: hover text} written by OneWooz staff in the Django
            # admin (Product field tooltips); read by {% field_tip %}.
            "field_tips": field_tips.tooltips_map(),
        }
    )
    return render(request, "store/product_form.html", context)


@login_required
@require_POST
def product_delete(request, site_id, product_id):
    site = get_owned_site(request, site_id)
    product = get_object_or_404(Product.objects.for_site(site), pk=product_id)
    product.soft_delete()
    activity.record(
        "store.product.deleted", request=request, site=site,
        target=product, target_kind="Product", label=product.title,
    )
    messages.success(request, f"“{product.title}” removed.")
    return redirect("store:products", site_id=site.id)


# --------------------------------------------------------------------------
# Inventory & out of stock
# --------------------------------------------------------------------------
@login_required
def inventory(request, site_id):
    site = get_owned_site(request, site_id)
    products = list(
        Product.objects.for_site(site)
        .filter(is_deleted=False)
        .select_related("category")
        .prefetch_related("images")
    )

    # Units sold this calendar month across paid/shipped/delivered orders 
    # "live inventory powered by your D2C orders", exactly like the reference.
    now = timezone.localtime(timezone.now())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        month_next = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_next = month_start.replace(month=month_start.month + 1)

    from commerce.models import OrderItem

    sold_rows = (
        OrderItem.objects.for_site(site)
        .filter(
            order__created_at__gte=month_start,
            order__created_at__lt=month_next,
            order__status__in=["paid", "shipped", "delivered"],
        )
        .values("product_id")
        .annotate(total_sold=Sum("quantity"))
    )
    sold_map = {str(r["product_id"]): int(r["total_sold"] or 0) for r in sold_rows}

    for p in products:
        p.available_inventory = p.stock_on_hand
        p.sold_this_month = sold_map.get(str(p.id), 0)

    context = store_context(request, site, "inventory")
    context.update(
        {
            "products": products,
            "categories_all": Category.objects.for_site(site).filter(is_deleted=False),
            "inventory_month_label": now.strftime("%b %Y"),
            "currency": site.currency,
        }
    )
    return render(request, "store/inventory.html", context)


@login_required
@require_POST
def product_restock(request, site_id, product_id):
    """Set inventory for a product, one variant combination, or all of them.

    Simple products set ``inventory``; variant products set the combo's
    ``inventory`` (a specific ``variant_key`` or all when none given). Accepts
    JSON or form-encoded ``quantity`` (+ optional ``variant_key``).
    """
    site = get_owned_site(request, site_id)
    product = get_object_or_404(Product.objects.for_site(site).filter(is_deleted=False), pk=product_id)

    qty = variant_key = None
    if "application/json" in (request.content_type or ""):
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            body = {}
        qty = body.get("quantity")
        variant_key = body.get("variant_key") or None
    if qty is None:
        qty = request.POST.get("quantity")
    if variant_key is None:
        variant_key = request.POST.get("variant_key") or None
    try:
        quantity = max(0, int(qty))
    except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid quantity"}, status=400)

    combos = product.variant_combinations
    if isinstance(combos, dict) and all(str(k).isdigit() for k in combos.keys()):
        combos = list(combos.values())

    if combos and isinstance(combos, list):
        updated, matched = [], False
        for comb in combos:
            if not isinstance(comb, dict):
                updated.append(comb)
                continue
            c = dict(comb)
            if variant_key is None or str(c.get("key")) == str(variant_key):
                c["inventory"] = quantity
                matched = True
            updated.append(c)
        if variant_key is not None and not matched:
            return JsonResponse({"status": "error", "message": "Variant not found"}, status=404)
        product.variant_combinations = updated
        product.inventory = None
        product.save(update_fields=["variant_combinations", "inventory", "updated_at"])
        new_total = sum(int((c or {}).get("inventory") or 0) for c in updated if isinstance(c, dict))
    else:
        product.variant_combinations = None
        product.inventory = quantity
        product.save(update_fields=["inventory", "variant_combinations", "updated_at"])
        new_total = int(product.inventory or 0)

    activity.record(
        "store.inventory.updated", request=request, site=site,
        target=product, target_kind="Product", label=product.title,
        changes={"new_total": new_total},
    )
    _alert_low_stock(site, product, new_total)
    return JsonResponse(
        {"status": "success", "quantity": quantity, "new_total": new_total, "variant_key": variant_key or ""}
    )


def _alert_low_stock(site, product, new_total: int) -> None:
    """Notify the owner's bell when a product is now out of / low on stock.

    Fires when the on-hand total is at or below the product's low-stock threshold
    (default 5). De-duplicated: it won't stack a fresh alert for a product that
    already has an unread stock alert, so repeated small edits don't spam the bell.
    """
    try:
        threshold = int(getattr(product, "low_stock_threshold", 5) or 5)
        if new_total > threshold:
            return
        from accounts import notify
        from accounts.models import Notification
        from django.urls import reverse

        owner = getattr(site, "owner", None)
        if owner is None:
            return
        headline = (
            f"Out of stock: {product.title}"
            if new_total <= 0
            else f"Low stock: {product.title}"
        )
        # Don't re-alert while an unread stock notice for this product stands.
        if Notification.objects.filter(
            user=owner, kind="stock", is_read=False, headline=headline
        ).exists():
            return
        if new_total <= 0:
            desc = f"“{product.title}” is now out of stock. Restock it to keep selling."
        else:
            desc = f"Only {new_total} left of “{product.title}”. Consider restocking soon."
        try:
            action_url = reverse("store:inventory", kwargs={"site_id": site.id})
        except Exception:
            action_url = ""
        notify.push_to_owner(
            site, headline=headline, description=desc,
            kind="stock", level="warning", action_url=action_url,
        )
    except Exception:
        pass


def _stock_rows(products):
    """``(out_of_stock, running_low)`` rows for the Out of stock page.

    A product without variations is one row (its own stock). A product with
    variations is judged per variation: each sold-out variation is its own
    "out of stock" row, and each variation at or under the product's
    low-stock threshold a "running low" row, so a single size or colour
    hitting 0 shows up even while the others still have stock. Stock lives in
    a JSON blob, so this runs in Python over a bounded scan.
    """
    from commerce.cart import _variant_label, combos_of

    out, low = [], []
    for p in products:
        threshold = int(p.low_stock_threshold or 0)
        combos = combos_of(p)
        if not combos:
            on_hand = int(p.inventory or 0)
            row = {"product": p, "variant": "", "on_hand": on_hand, "threshold": threshold}
            if on_hand <= 0:
                out.append(row)
            elif on_hand <= threshold:
                low.append(row)
            continue
        for combo in combos:
            try:
                on_hand = max(0, int(combo.get("inventory") or 0))
            except (TypeError, ValueError):
                on_hand = 0
            row = {
                "product": p,
                "variant": _variant_label(combo),
                "on_hand": on_hand,
                "threshold": threshold,
            }
            if on_hand <= 0:
                out.append(row)
            elif on_hand <= threshold:
                low.append(row)
    return out, low


@login_required
def out_of_stock(request, site_id):
    site = get_owned_site(request, site_id)
    live = Product.objects.for_site(site).filter(is_deleted=False, is_active=True).select_related("category")
    out, low = _stock_rows(live[:1000])

    context = store_context(request, site, "out_of_stock")
    context.update({"out_of_stock": out, "low_stock": low})
    return render(request, "store/out_of_stock.html", context)


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
def _d2c_config(site) -> dict:
    return dict((site.content or {}).get("d2c_config") or {})


def _save_d2c_config(site, cfg) -> None:
    content = site.content or {}
    content["d2c_config"] = cfg
    site.content = content
    site.save(update_fields=["content"])


@login_required
def categories(request, site_id):
    site = get_owned_site(request, site_id)
    qs = list(
        display_ordered_categories(
            Category.objects.for_site(site)
            .filter(is_deleted=False)
            .select_related("parent")
        )
    )
    cfg = _d2c_config(site)
    context = store_context(request, site, "categories")
    context.update(
        {
            "categories": qs,
            "categories_count": len(qs),
            # 'on' by default (a mega menu is the storefront default) unless
            # explicitly turned off  matches the reference's tri-state string.
            "mega_menu_enabled": cfg.get("categories_mega_menu_enabled", "on") != "off",
            "highlight_category_id": str(cfg.get("mega_highlight_category_id") or ""),
            "trusted_icons_enabled": bool(cfg.get("category_as_certificates_enabled")),
            "trusted_icons": cfg.get("category_certificate_icons") or [],
        }
    )
    return render(request, "store/categories.html", context)


@login_required
@require_POST
def toggle_categories_mega_menu(request, site_id):
    site = get_owned_site(request, site_id)
    cfg = _d2c_config(site)
    enabled = str(request.POST.get("enabled")).strip().lower() in ("1", "true", "yes", "on")
    cfg["categories_mega_menu_enabled"] = "on" if enabled else "off"
    _save_d2c_config(site, cfg)
    return JsonResponse({"success": True, "enabled": enabled})


@login_required
@require_POST
def category_set_highlight(request, site_id, category_id):
    """Star / un-star a category as the storefront Mega Highlight (one only)."""
    site = get_owned_site(request, site_id)
    category = get_object_or_404(Category.objects.for_site(site).filter(is_deleted=False), pk=category_id)
    cfg = _d2c_config(site)
    current = str(cfg.get("mega_highlight_category_id") or "")
    if current == str(category.id):
        cfg["mega_highlight_category_id"] = ""  # toggle off
        highlighted = False
    else:
        cfg["mega_highlight_category_id"] = str(category.id)
        highlighted = True
    _save_d2c_config(site, cfg)
    return JsonResponse({"success": True, "highlighted": highlighted, "category_id": str(category.id)})


@login_required
@require_POST
def toggle_trusted_icons(request, site_id):
    site = get_owned_site(request, site_id)
    cfg = _d2c_config(site)
    enabled = str(request.POST.get("enabled")).strip().lower() in ("1", "true", "yes", "on")
    cfg["category_as_certificates_enabled"] = enabled
    _save_d2c_config(site, cfg)
    return JsonResponse({"success": True, "enabled": enabled})


@login_required
@require_POST
def save_trusted_icons(request, site_id):
    """Persist the trusted-icon rows ({label, image}) posted from the manager."""
    site = get_owned_site(request, site_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        rows = payload.get("icons") or []
    except (ValueError, UnicodeDecodeError):
        rows = []
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        image = (row.get("image") or "").strip()
        label = (row.get("label") or "").strip()[:80]
        if image:
            cleaned.append({"label": label, "image": image})
    cfg = _d2c_config(site)
    cfg["category_certificate_icons"] = cleaned
    _save_d2c_config(site, cfg)
    return JsonResponse({"success": True, "icons": cleaned})


@login_required
def category_form(request, site_id, category_id=None):
    site = get_owned_site(request, site_id)
    instance = None
    if category_id:
        instance = get_object_or_404(
            Category.objects.for_site(site).filter(is_deleted=False), pk=category_id
        )

    if request.method == "POST":
        form = CategoryForm(request.POST, request.FILES, instance=instance, site=site)
        if form.is_valid():
            category = form.save(commit=False)
            category.site = site
            # Category image picked from the library (copied server-side). A
            # direct file upload on the model field still wins if one was sent.
            asset_id = request.POST.get("media_image_category")
            if asset_id and not request.FILES.get("image"):
                asset = (
                    MediaAsset.objects.for_site(site)
                    .filter(pk=asset_id, kind=MediaAsset.Kind.IMAGE)
                    .first()
                )
                if asset is not None:
                    try:
                        category.image = clone_asset_file(asset, f"categories/{site.id}")
                    except Exception:
                        pass
            category.save()
            activity.record(
                "store.category.updated" if instance else "store.category.created",
                request=request, site=site, target=category,
                target_kind="Category", label=category.name,
            )
            messages.success(request, f"“{category.name}” saved.")
            return redirect("store:categories", site_id=site.id)
    else:
        form = CategoryForm(instance=instance, site=site)

    # The page's public address, shown under the name: the slug follows the
    # name on save, and the storefront links and sitemap follow the slug.
    if site.custom_domain and site.custom_domain_verified:
        storefront_root = f"https://{site.custom_domain}/"
    else:
        storefront_root = site.storefront_url(request)
    context = store_context(request, site, "categories")
    context.update({
        "form": form, "category": instance, "is_edit": bool(instance),
        "category_url_base": storefront_root.rstrip("/") + "/category/",
    })
    return render(request, "store/category_form.html", context)


@login_required
@require_POST
def category_delete(request, site_id, category_id):
    site = get_owned_site(request, site_id)
    category = get_object_or_404(Category.objects.for_site(site), pk=category_id)
    category.soft_delete()
    activity.record(
        "store.category.deleted", request=request, site=site,
        target=category, target_kind="Category", label=category.name,
    )
    messages.success(request, f"“{category.name}” removed.")
    return redirect("store:categories", site_id=site.id)


# --------------------------------------------------------------------------
# Reviews (D2C)
# --------------------------------------------------------------------------
@login_required
def reviews(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Review.objects.for_site(site).filter(is_deleted=False).select_related("product")
    state = request.GET.get("state") or ""
    if state == "pending":
        qs = qs.filter(approved=False, reported=False)
    elif state == "approved":
        qs = qs.filter(approved=True)
    elif state == "reported":
        qs = qs.filter(reported=True)

    context = store_context(request, site, "reviews")
    context.update({"reviews": _paginate(request, qs), "active_state": state})
    return render(request, "store/reviews.html", context)


@login_required
@require_POST
def review_moderate(request, site_id, review_id):
    site = get_owned_site(request, site_id)
    review = get_object_or_404(Review.objects.for_site(site), pk=review_id)
    action = request.POST.get("action") or ""
    if action == "approve":
        review.approved = True
        review.reported = False
        review.save(update_fields=["approved", "reported", "updated_at"])
    elif action == "hide":
        review.approved = False
        review.save(update_fields=["approved", "updated_at"])
    elif action == "delete":
        review.soft_delete()
    activity.record(
        "store.review.moderated", request=request, site=site,
        target=review, target_kind="Review",
        label=getattr(getattr(review, "product", None), "title", "") or "Review",
        changes={"action": action},
    )
    messages.success(request, "Review updated.")
    return redirect("store:reviews", site_id=site.id)


# --------------------------------------------------------------------------
# Store-made trust badges (product form "+" tile)
# --------------------------------------------------------------------------
def _json_body(request):
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


@require_POST
@login_required
def trust_badge_create(request, site_id):
    """Create one of the store's own trust badges: ``{name, icon}``.

    The name is at most two words; the icon must be one of the built-in set
    (commerce.trust_icons)  nothing is uploaded. Saved on the store's content
    (``custom_trust_badges``) and returned so the picker can add and tick it."""
    site = get_owned_site(request, site_id)
    data = _json_body(request)
    name, error = trust_badges.clean_name(data.get("name"))
    icon = str(data.get("icon") or "").strip()
    if error:
        return JsonResponse({"ok": False, "error": error}, status=400)
    if icon not in trust_icons.ICON_MAP:
        return JsonResponse({"ok": False, "error": "Choose an icon for the badge."}, status=400)
    if trust_badges.is_removed(name):
        return JsonResponse({"ok": False, "error": "That badge isn’t available. Try another name."}, status=400)

    existing = {trust_badges.norm(b["name"]) for b in trust_badges.options(site)}
    if trust_badges.norm(name) in existing:
        return JsonResponse({"ok": False, "error": "A badge with that name already exists."}, status=400)

    content = dict(site.content or {})
    own = trust_badges.custom_badges(site)
    if len(own) >= trust_badges.MAX_CUSTOM_PER_STORE:
        return JsonResponse(
            {"ok": False, "error": f"You can create up to {trust_badges.MAX_CUSTOM_PER_STORE} badges."},
            status=400,
        )
    own.append({"name": name, "icon": icon})
    content["custom_trust_badges"] = own
    site.content = content
    site.save(update_fields=["content", "updated_at"])

    activity.record(
        "store.trust_badge.created", request=request, site=site,
        target=site, target_kind="Site", label=name,
    )
    return JsonResponse({
        "ok": True,
        "badge": {"name": name, "icon": icon, "svg": str(trust_icons.svg(icon)), "custom": True},
    })


@require_POST
@login_required
def trust_badge_delete(request, site_id):
    """Delete one of the store's own badges by name, and take it off every
    product of this store that showed it."""
    site = get_owned_site(request, site_id)
    target = trust_badges.norm(_json_body(request).get("name"))
    own = trust_badges.custom_badges(site)
    kept = [b for b in own if trust_badges.norm(b["name"]) != target]
    if len(kept) == len(own):
        return JsonResponse({"ok": False, "error": "Badge not found."}, status=404)
    content = dict(site.content or {})
    content["custom_trust_badges"] = kept
    site.content = content
    site.save(update_fields=["content", "updated_at"])
    for product in Product.objects.for_site(site).only("id", "trusted_icons"):
        icons = product.trusted_icons or []
        remaining = [n for n in icons if trust_badges.norm(n) != target]
        if len(remaining) != len(icons):
            product.trusted_icons = remaining
            product.save(update_fields=["trusted_icons"])
    activity.record(
        "store.trust_badge.deleted", request=request, site=site,
        target=site, target_kind="Site", label=target,
    )
    return JsonResponse({"ok": True})
