"""A store's ``/llms.txt``, generated with the OpenAI API from its own content.

Flow
----
1. The owner saves a section in Edit Content, or adds, edits, (de)activates or
   deletes a product or category (signals in :mod:`prompts.signals`).
   :func:`schedule` queues one debounced Celery job per store.
2. The job (:func:`run`) checks :data:`REQUIREMENTS`. A store that does not meet
   every one is recorded as "Waiting for required content" and nothing is sent
   to OpenAI.
3. A ready store's facts are collected within :data:`LIMITS`, hashed, and, only
   if the hash changed since the last file, sent to ChatGPT with the active
   admin prompt (Django admin → Prompts, type "LLMs.txt generation").
4. The reply is cleaned (:func:`sanitise`): no em or en dashes, links only to
   the store's own pages, size capped. It is published at ``/llms.txt`` on the
   store's verified custom domain.

A daily Beat job re-checks every published store so catalogue edits made
outside Edit Content (new products, price changes) are picked up too. Because
of the hash, a store whose data did not change costs no API call.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings
from django.utils.html import strip_tags

logger = logging.getLogger("onewooz.llms_txt")

# ---------------------------------------------------------------------------
# The limits
# ---------------------------------------------------------------------------
#: What a store must have before its llms.txt is generated. Every check must
#: pass. Lengths are counted on plain text (HTML tags removed, spaces collapsed).
REQUIREMENTS = {
    "published": "Store is live on its own verified custom domain (llms.txt is only served there)",
    "name": "Store name set (2 to 60 characters, not the default 'My store')",
    "description": "SEO description set in Edit Content → SEO (70 to 320 characters)",
    "about": "About page text in Edit Content → About (at least 300 characters)",
    "contact": "Contact email, plus a phone number or postal address",
    "products": "At least 3 live products, and at least 3 of them with a description of 80+ characters",
    "category": "At least 1 category that has a live product in it",
    "policies": "Refund policy and Shipping policy written in Edit Content → Policies (at least 200 characters each)",
}

#: How much of each kind of content is sent to the model, so every request has
#: a bounded size and cost whatever the store's size.
LIMITS = {
    "description_chars": 320,
    "about_chars": 2000,
    "products": 60,
    "product_description_chars": 220,
    "categories": 30,
    "category_names": 100,
    "category_description_chars": 160,
    "blog_posts": 15,
    "blog_excerpt_chars": 220,
    "core_pages": 20,
    "core_page_summary_chars": 220,
    "faqs": 12,
    "faq_question_chars": 200,
    "faq_answer_chars": 400,
    "policy_summary_chars": 600,
    # The generated file itself.
    "output_chars": 12000,
}

MIN_NAME, MAX_NAME = 2, 60
MIN_DESCRIPTION, MAX_DESCRIPTION = 70, 320
MIN_ABOUT = 300
MIN_PRODUCTS, MIN_DESCRIBED_PRODUCTS, MIN_PRODUCT_DESCRIPTION = 3, 3, 80
MIN_POLICY = 200

#: Seconds between the owner's last save and the generation run, so a burst of
#: autosaves becomes one API call.
DEBOUNCE_SECONDS = 120
_TIMEOUT = 120

_STOREFRONT_URLCONF = "onewooz.urls.storefront"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _text(value) -> str:
    """Plain, single-spaced text from an HTML fragment or any value."""
    return " ".join(html_lib.unescape(strip_tags(str(value or ""))).split())


def _clip(text: str, limit: int) -> str:
    text = _text(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.")
    return f"{cut}…"


def _origin(site) -> str:
    return f"https://{(site.custom_domain or '').strip().lower().rstrip('/')}"


def _url(site, name: str, **kwargs) -> str:
    from django.urls import reverse

    return _origin(site) + reverse(name, urlconf=_STOREFRONT_URLCONF, kwargs=kwargs or None)


def _content(site) -> dict:
    return site.content if isinstance(site.content, dict) else {}


def _store_name(site) -> str:
    return _text(_content(site).get("site_name") or site.name)


def _contact(site) -> dict:
    content = _content(site)
    ci = content.get("contact_info") if isinstance(content.get("contact_info"), dict) else {}
    address = ", ".join(
        p for p in (_text(ci.get(k)) for k in ("address", "city", "state", "postal_code", "country")) if p
    )
    return {
        "email": _text(ci.get("contact_email") or ci.get("email") or site.support_email),
        "phone": _text(ci.get("mobile_number") or ci.get("phone") or site.support_phone),
        "address": address,
    }


def _home_description(content: dict) -> str:
    """The store's own description: the SEO tab's home-page meta description,
    whichever SEO mode the owner uses ("Same for every page" stores it in
    ``seo``; "Different" in ``seo_pages.home``)."""
    seo = content.get("seo") if isinstance(content.get("seo"), dict) else {}
    pages = content.get("seo_pages") if isinstance(content.get("seo_pages"), dict) else {}
    home = pages.get("home") if isinstance(pages.get("home"), dict) else {}
    return _text(seo.get("meta_description") or home.get("meta_description") or content.get("meta_description"))


def _live_products(site):
    from commerce.models import Product

    # Unordered + a narrow column set: never ask MySQL to sort product rows.
    return (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by()
        .only("id", "title", "slug", "description", "price", "mrp", "category_id", "updated_at")
    )


def _category_product_counts(site) -> dict:
    """Live products per category id, counting a product under its primary
    category and every additional category it is tagged with (once each)."""
    live = _live_products(site)
    members: dict = {}
    for pid, cid in live.values_list("id", "category_id"):
        if cid:
            members.setdefault(cid, set()).add(pid)
    for pid, cid in live.values_list("id", "categories__id"):
        if cid:
            members.setdefault(cid, set()).add(pid)
    return {cid: len(pids) for cid, pids in members.items()}


def _stocked_category_ids(site, products) -> set:
    """Categories holding a live product, by its primary category or by the
    additional-categories multi-select."""
    ids = {p.category_id for p in products if p.category_id}
    ids |= set(_live_products(site).values_list("categories__id", flat=True))
    ids.discard(None)
    return ids


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
def readiness(site) -> list[dict]:
    """Each requirement with whether this store meets it: ``[{key, label, ok}]``."""
    from commerce.models import Category

    content = _content(site)
    contact = _contact(site)

    name = _store_name(site)
    description = _home_description(content)
    about = _text(content.get("about_content") or content.get("about_section_content"))

    products = list(_live_products(site)[:500])
    with_slug = [p for p in products if (p.slug or "").strip()]
    described = [p for p in with_slug if len(_text(p.description)) >= MIN_PRODUCT_DESCRIPTION]

    stocked_category_ids = _stocked_category_ids(site, products)
    if stocked_category_ids:
        stocked_category_ids = set(
            Category.objects.for_site(site)
            .filter(id__in=stocked_category_ids, is_active=True, is_deleted=False)
            .values_list("id", flat=True)[:1]
        )

    published = (
        bool(getattr(site, "custom_domain_verified", False))
        and bool((site.custom_domain or "").strip())
        and (bool(site.is_live) or bool(getattr(site, "custom_domain_verified", False)))
        and not site.maintenance_mode
    )

    checks = {
        "published": published,
        "name": MIN_NAME <= len(name) <= MAX_NAME and name.lower() != "my store",
        "description": MIN_DESCRIPTION <= len(description) <= MAX_DESCRIPTION,
        "about": len(about) >= MIN_ABOUT,
        "contact": bool(contact["email"]) and bool(contact["phone"] or contact["address"]),
        "products": len(with_slug) >= MIN_PRODUCTS and len(described) >= MIN_DESCRIBED_PRODUCTS,
        "category": bool(stocked_category_ids),
        "policies": all(
            len(_text(content.get(k))) >= MIN_POLICY for k in ("refund_policy", "shipping_policy")
        ),
    }
    return [{"key": k, "label": REQUIREMENTS[k], "ok": bool(checks[k])} for k in REQUIREMENTS]


def missing_requirements(site) -> list[str]:
    return [c["label"] for c in readiness(site) if not c["ok"]]


# ---------------------------------------------------------------------------
# Data collection (within LIMITS)
# ---------------------------------------------------------------------------
def collect(site) -> tuple[dict, list[str]]:
    """The store facts sent to the model, and the exact URLs it may link to."""
    from commerce.models import Category

    content = _content(site)
    urls: list[str] = []

    def link(name, **kwargs):
        try:
            u = _url(site, name, **kwargs)
        except Exception:
            return ""
        if u not in urls:
            urls.append(u)
        return u

    categories = list(
        Category.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by()
        .only("id", "name", "slug", "description")[:200]
    )
    cat_names = {c.id: _text(c.name) for c in categories}

    products = list(_live_products(site)[:500])
    # Described products first, then the most recently updated.
    products.sort(key=lambda p: (len(_text(p.description)) < MIN_PRODUCT_DESCRIPTION, -(p.updated_at.timestamp() if p.updated_at else 0)))
    counts = _category_product_counts(site)
    stocked = {cid for cid, n in counts.items() if n}
    live_categories = [c for c in categories if (c.slug or "").strip() and c.id in stocked]
    # Biggest categories first, so the most representative ones survive the cap.
    live_categories.sort(key=lambda c: (-counts.get(c.id, 0), _text(c.name).lower()))

    data = {
        "store_name": _store_name(site),
        "website": link("public_website"),
        "business_model": "B2B" if getattr(site, "is_b2b", False) and not getattr(site, "is_d2c", True) else (
            "D2C and B2B" if getattr(site, "is_b2b", False) else "D2C"),
        "country": "India",
        "currency": "INR",
        "description": _clip(_home_description(content), LIMITS["description_chars"]),
        "about": {
            "url": link("public_about"),
            "text": _clip(content.get("about_content") or content.get("about_section_content"), LIMITS["about_chars"]),
        },
        "contact": {**_contact(site), "url": link("public_enquiry")},
        "pages": {
            "all_products": link("public_all_products"),
            "categories": link("public_categories"),
        },
        # The whole live catalogue at a glance: exact counts and every category
        # name, even when the detailed lists below are capped. Inactive or
        # deleted products, and categories with no live product, never count.
        "catalogue": {
            "live_products": _live_products(site).count(),
            "categories_with_products": len(live_categories),
            "category_names": [_text(c.name) for c in live_categories][: LIMITS["category_names"]],
        },
        "categories": [
            {
                "name": _text(c.name),
                "url": link("public_category_detail", category_slug=c.slug),
                "live_products": counts.get(c.id, 0),
                "description": _clip(c.description, LIMITS["category_description_chars"]),
            }
            for c in live_categories
        ][: LIMITS["categories"]],
        "products": [
            {
                "name": _text(p.title),
                "url": link("public_product_detail", product_slug=p.slug),
                "category": cat_names.get(p.category_id, ""),
                "price_inr": str(p.price),
                "description": _clip(p.description, LIMITS["product_description_chars"]),
            }
            for p in products
            if (p.slug or "").strip()
        ][: LIMITS["products"]],
        "policies": [],
        "faqs": [],
        "blog_posts": [],
        "guides": [],
    }

    for key, name, label in (
        ("refund_policy", "public_refund", "Returns and refunds"),
        ("shipping_policy", "public_shipping", "Shipping"),
        ("privacy_policy", "privacy_policy_page", "Privacy"),
        ("terms_conditions", "public_terms", "Terms and conditions"),
    ):
        text = _clip(content.get(key), LIMITS["policy_summary_chars"])
        if text:
            data["policies"].append({"title": label, "url": link(name), "summary": text})

    for f in (content.get("faqs") or [])[: LIMITS["faqs"]]:
        if isinstance(f, dict) and _text(f.get("question")) and _text(f.get("answer")):
            data["faqs"].append({
                "question": _clip(f.get("question"), LIMITS["faq_question_chars"]),
                "answer": _clip(f.get("answer"), LIMITS["faq_answer_chars"]),
            })

    for post in content.get("blog_posts") or []:
        if len(data["blog_posts"]) >= LIMITS["blog_posts"]:
            break
        if not isinstance(post, dict) or not (post.get("slug") or "").strip():
            continue
        if str(post.get("status") or "published") != "published":
            continue
        data["blog_posts"].append({
            "title": _text(post.get("title")),
            "url": link("public_blog_detail", blog_slug=post["slug"].strip()),
            "summary": _clip(post.get("excerpt") or post.get("content"), LIMITS["blog_excerpt_chars"]),
        })

    for page in content.get("core_pages") or []:
        if len(data["guides"]) >= LIMITS["core_pages"]:
            break
        slug = str((page or {}).get("slug") or "").strip("/") if isinstance(page, dict) else ""
        if not slug:
            continue
        data["guides"].append({
            "title": _text(page.get("title")),
            "url": link("public_core_page", page_slug=slug),
            "summary": _clip(page.get("subtitle") or page.get("content"), LIMITS["core_page_summary_chars"]),
        })
    if data["guides"]:
        data["pages"]["guides_index"] = link("public_core_pages")

    return data, urls


def fingerprint(data: dict, prompt, model: str) -> str:
    raw = json.dumps(
        {"data": data, "prompt": getattr(prompt, "prompt", ""), "model": model},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Output contract + cleaning
# ---------------------------------------------------------------------------
#: Appended after the store data on every request, whatever the admin prompt
#: says, so the file is always valid and safe to publish.
OUTPUT_CONTRACT = """\
OUTPUT RULES (always apply):
1. Return only the llms.txt file as plain Markdown, following llmstxt.org: the first line is "# {name}", then one "> " summary paragraph, then sections with "## " headings containing "- [Title](URL): short note" list items.
2. Use only facts from STORE DATA. Never invent prices, numbers, awards, certifications, reviews, delivery times or claims.
3. Link only to URLs from ALLOWED URLS, copied exactly. No other links.
4. Never use em dashes or en dashes. Use commas, colons or full stops instead.
5. No code fences, no HTML, no commentary before or after the file. At most {max_chars} characters.
"""

_DASH_BETWEEN_DIGITS = re.compile(r"(?<=\d)\s*[‒–—―−]\s*(?=\d)")
_DASH_ANY = re.compile(r"\s*[‒–—―]\s*")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def sanitise(text: str, *, name: str, allowed_urls: list[str]) -> str:
    """Make a model reply safe to publish: no dashes, own links only, capped."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # No em/en dashes anywhere: a range keeps a hyphen, anything else a comma.
    text = _DASH_BETWEEN_DIGITS.sub("-", text)
    text = _DASH_ANY.sub(", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^(\s*[-*]\s*), ", r"\1", text, flags=re.M)

    # Links: keep only the store's own URLs. A list item none of whose links
    # is the store's own is dropped; otherwise a foreign link keeps its text
    # and loses the link.
    allowed = {_norm_url(u) for u in allowed_urls}
    out_lines = []
    for line in text.split("\n"):
        links = _MD_LINK.findall(line)
        bad = [u for _, u in links if _norm_url(u) not in allowed]
        if bad and len(bad) == len(links) and line.lstrip().startswith(("-", "*")):
            continue
        if bad:
            line = _MD_LINK.sub(lambda m: m.group(0) if _norm_url(m.group(2)) in allowed else m.group(1), line)
        out_lines.append(line.rstrip())
    text = "\n".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # The title line is always the store's name.
    first, _, rest = text.partition("\n")
    if not first.startswith("# "):
        text = f"# {name}\n\n{text}"
    else:
        text = f"# {name}\n{rest}"

    limit = LIMITS["output_chars"]
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0]
    return text.strip() + "\n"


def _validate(text: str) -> str:
    """'' when the file is publishable, else the reason it is not."""
    if not text.startswith("# "):
        return "The reply has no '# Store name' title line."
    if "\n> " not in text and "\n>" not in text:
        return "The reply has no '> ' summary line."
    if "](" not in text:
        return "The reply links to none of the store's pages."
    if "—" in text or "–" in text:
        return "The reply still contains a dash after cleaning."
    return ""


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
def _post_json(url: str, body: dict, api_key: str | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {api_key or settings.OPENAI_API_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return ((payload.get("error") or {}).get("message") or "").strip()
    except Exception:
        return ""


def _complete(system_prompt: str, user_message: str, model: str, *, json_mode: bool = False,
              temperature: float = 0.3, api_key: str | None = None, base_url: str | None = None) -> str:
    """One chat completion. ``json_mode`` asks the model for a single JSON
    object (OpenAI JSON mode); the caller still validates what comes back.
    ``api_key`` / ``base_url`` override the server settings (a marketplace
    app's own key, see marketplace.ai)."""
    url = f"{(base_url or settings.OPENAI_BASE_URL).rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        out = _post_json(url, body, api_key)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        # Some models accept only their default temperature: retry once without.
        if "temperature" in detail.lower():
            body.pop("temperature", None)
            try:
                out = _post_json(url, body, api_key)
            except urllib.error.HTTPError as exc2:
                raise RuntimeError(_error_detail(exc2) or f"OpenAI HTTP {exc2.code}") from exc2
        else:
            raise RuntimeError(detail or f"OpenAI HTTP {exc.code}") from exc
    return (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _record(site):
    from .models import StoreLlmsTxt

    record = StoreLlmsTxt.objects.for_site(site).first()
    if record is None:
        record = StoreLlmsTxt(site=site)
    return record


def schedule(site) -> None:
    """Queue a (debounced) generation for ``site``. Cheap: called on every
    Edit Content save and every product/category change; all real work happens
    in the Celery job."""
    schedule_site_id(site.pk)


def schedule_site_id(site_id) -> None:
    from django.core.cache import cache

    from .tasks import generate_store_llms_txt

    if not site_id:
        return
    try:
        if not cache.add(f"llms_txt:queued:{site_id}", 1, DEBOUNCE_SECONDS):
            return  # a run is already queued for this store
    except Exception:
        pass
    generate_store_llms_txt.apply_async(args=[str(site_id)], countdown=DEBOUNCE_SECONDS)


def run(site, *, force: bool = False) -> str:
    """Check, and if needed generate, ``site``'s llms.txt. Returns the status."""
    from django.utils import timezone

    from .models import Prompt, StoreLlmsTxt

    S = StoreLlmsTxt.Status
    record = _record(site)

    missing = missing_requirements(site)
    if missing:
        if record.status != S.NOT_READY or record.missing != missing:
            record.status, record.missing = S.NOT_READY, missing
            record.save()
        return record.status

    prompt = Prompt.active(Prompt.Kind.LLMS_TXT)
    if prompt is None or not getattr(settings, "OPENAI_API_KEY", ""):
        record.missing = []
        record.error = (
            "No active 'LLMs.txt generation' prompt in admin." if prompt is None
            else "OPENAI_API_KEY is not set on the server."
        )
        if record.status != S.READY:
            record.status = S.FAILED
        record.save()
        return record.status

    model = (prompt.model or "").strip() or settings.OPENAI_TEXT_MODEL
    data, urls = collect(site)
    fp = fingerprint(data, prompt, model)
    if not force and record.status == S.READY and record.fingerprint == fp:
        return record.status

    user_message = (
        "STORE DATA (JSON):\n"
        + json.dumps(data, ensure_ascii=False, indent=1)
        + "\n\nALLOWED URLS:\n"
        + "\n".join(f"- {u}" for u in urls)
        + "\n\n"
        + OUTPUT_CONTRACT.format(name=data["store_name"], max_chars=LIMITS["output_chars"])
    )
    try:
        reply = _complete(prompt.prompt, user_message, model)
        body = sanitise(reply, name=data["store_name"], allowed_urls=urls)
        problem = _validate(body)
        if problem:
            raise RuntimeError(problem)
    except Exception as exc:
        logger.warning("llms.txt generation failed for site %s: %s", site.pk, exc)
        record.error = str(exc)[:2000]
        record.missing = []
        # A store that already has a good file keeps serving it.
        if record.status != S.READY:
            record.status = S.FAILED
        record.save()
        return record.status

    record.status = S.READY
    record.body = body
    record.fingerprint = fp
    record.missing = []
    record.error = ""
    record.model_used = model[:120]
    record.prompt = prompt
    record.generated_at = timezone.now()
    record.save()
    return record.status


def published_body(site) -> str:
    """The file to serve at /llms.txt, or '' when there is none to serve."""
    from .models import StoreLlmsTxt

    record = (
        StoreLlmsTxt.objects.for_site(site)
        .filter(status=StoreLlmsTxt.Status.READY)
        .only("id", "body")
        .first()
    )
    return (record.body if record else "") or ""
