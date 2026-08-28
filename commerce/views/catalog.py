"""Catalogue management: products, categories, inventory, stock and reviews."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from commerce.forms import CategoryForm, ProductForm
from commerce.models import Category, Product, ProductImage, Review


def _paginate(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page") or 1)


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------
@login_required
def products(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Product.objects.for_site(site).filter(is_deleted=False).select_related("category")

    q = (request.GET.get("q") or "").strip()
    category = request.GET.get("category") or ""
    status = request.GET.get("status") or ""
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(sku__icontains=q))
    if category:
        qs = qs.filter(category_id=category)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    context = store_context(request, site, "products")
    context.update(
        {
            "products": _paginate(request, qs),
            "categories": Category.objects.for_site(site).filter(is_deleted=False),
            "q": q,
            "active_category": category,
            "active_status": status,
        }
    )
    return render(request, "store/products.html", context)


def _split_csv(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


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
        "trusted_icons": request.POST.getlist("trusted_icons")[:4],
        "variants": variants or None,
        "d2c_details": d2c_details,
    }


def _product_prefill(product):
    """Flatten a product's JSON columns back into the shapes the template's
    repeatable rows expect, so editing shows what was saved."""
    if product is None:
        return {}
    details = product.d2c_details or {}
    return {
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


# Badge options offered on the product form (parity with the reference).
TRUSTED_BADGES = [
    "Pan India Delivery", "6 month warranty", "1 year warranty", "Non returnable",
    "Cash On Delivery", "Secure Payment", "Free Shipping", "Free Return",
    "Easy Returns", "Fast Delivery", "Secure Packaging",
]


@login_required
def product_form(request, site_id, product_id=None):
    site = get_owned_site(request, site_id)
    instance = None
    if product_id:
        instance = get_object_or_404(
            Product.objects.for_site(site).filter(is_deleted=False), pk=product_id
        )

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=instance, site=site)
        if form.is_valid():
            product = form.save(commit=False)
            product.site = site
            for attr, value in _collect_json_fields(request).items():
                setattr(product, attr, value)
            product.save()
            form.save_m2m()

            # New gallery images (up to 7). Each provided slot becomes an ordered
            # ProductImage; the first is the main image.
            for i in range(1, 8):
                upload = request.FILES.get(f"image{i}")
                if upload:
                    ProductImage.objects.create(
                        site=site, product=product, image=upload, order=i
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
            "trusted_badges": TRUSTED_BADGES,
            "prefill": _product_prefill(instance),
            "existing_images": instance.images.for_site(site) if instance else [],
        }
    )
    return render(request, "store/product_form.html", context)


@login_required
@require_POST
def product_delete(request, site_id, product_id):
    site = get_owned_site(request, site_id)
    product = get_object_or_404(Product.objects.for_site(site), pk=product_id)
    product.soft_delete()
    messages.success(request, f"“{product.title}” removed.")
    return redirect("store:products", site_id=site.id)


# --------------------------------------------------------------------------
# Inventory & out of stock
# --------------------------------------------------------------------------
@login_required
def inventory(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Product.objects.for_site(site).filter(is_deleted=False).select_related("category")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(sku__icontains=q))

    context = store_context(request, site, "inventory")
    context.update({"products": _paginate(request, qs), "q": q})
    return render(request, "store/inventory.html", context)


@login_required
def out_of_stock(request, site_id):
    site = get_owned_site(request, site_id)
    live = Product.objects.for_site(site).filter(is_deleted=False, is_active=True).select_related("category")
    # Stock spans a JSON variant blob, so filter in Python over a bounded scan.
    rows = [p for p in live[:1000] if p.is_out_of_stock or p.is_low_stock]
    out = [p for p in rows if p.is_out_of_stock]
    low = [p for p in rows if p.is_low_stock]

    context = store_context(request, site, "out_of_stock")
    context.update({"out_of_stock": out, "low_stock": low})
    return render(request, "store/out_of_stock.html", context)


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
@login_required
def categories(request, site_id):
    site = get_owned_site(request, site_id)
    qs = (
        Category.objects.for_site(site)
        .filter(is_deleted=False)
        .select_related("parent")
    )
    context = store_context(request, site, "categories")
    context.update({"categories": qs})
    return render(request, "store/categories.html", context)


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
            category.save()
            messages.success(request, f"“{category.name}” saved.")
            return redirect("store:categories", site_id=site.id)
    else:
        form = CategoryForm(instance=instance, site=site)

    context = store_context(request, site, "categories")
    context.update({"form": form, "category": instance, "is_edit": bool(instance)})
    return render(request, "store/category_form.html", context)


@login_required
@require_POST
def category_delete(request, site_id, category_id):
    site = get_owned_site(request, site_id)
    category = get_object_or_404(Category.objects.for_site(site), pk=category_id)
    category.soft_delete()
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
    messages.success(request, "Review updated.")
    return redirect("store:reviews", site_id=site.id)
