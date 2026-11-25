"""Magic SEO share images: ChatGPT images for pages that have none.

A page "has no share image" when neither its Open Graph image nor its X
(Twitter) card image is set and the page has no picture of its own (product
photo, category image, blog cover, About image), so the storefront would fall
back to the store logo, or to nothing. A logo makes a poor link preview: it is
square, small and says nothing about the page. For those pages a Magic SEO run
asks the OpenAI image API (the Magic SEO app's image model, set in Django
admin > Apps, else ``settings.OPENAI_IMAGE_MODEL``) for a wide picture
drawn from the page's own content, crops it to the 1.91:1 card shape every
network uses (1200 x 628), saves it as a JPEG in the store's media, and uses it
for both the OG image and the X card image.

What is never generated
-----------------------
* Product pages: a made-up photo of a product misrepresents what the shopper
  will receive. A product with no photo keeps the brand fallback.
* Any page whose share image the owner set, or whose own picture exists.
* Text inside the image: model-drawn lettering is often misspelt, and the
  title already sits under the card.

All policy pages (privacy, terms, refund, shipping, custom policies) share one
image, and each run makes at most ``MAX_IMAGES`` images, so the cost of a run
stays small and predictable. Generated images are kept like owner-set ones on
later runs, so a page is only ever drawn once.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import secrets
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger("onewooz.magic_seo")

#: Images one run may create (policies count once).
MAX_IMAGES = 20
#: Image API calls in flight at once.
WORKERS = 3
#: Seconds one image call may take (they are slow).
TIMEOUT = 180
#: No new image is started after this long into the run, so the job finishes
#: inside its Celery time limit (prompts.tasks.MAGIC_SEO_TIME_LIMIT).
RUN_BUDGET_SECONDS = 22 * 60
#: The card shape Facebook, WhatsApp, LinkedIn and X all display well.
CARD_SIZE = (1200, 628)
JPEG_QUALITY = 86
#: gpt-image quality: low / medium / high. Medium is sharp at card size.
QUALITY = getattr(settings, "MAGIC_SEO_IMAGE_QUALITY", "medium")

#: Page types (magic_seo._PAGE_TYPES values) drawn, in priority order.
_PRIORITY = {
    "home page": 0, "about page": 1, "all products listing": 2, "category index": 3,
    "blog index": 4, "guides index": 5, "contact page": 6, "certificates page": 7,
    "blog post": 10, "content page": 11, "category page": 12, "policy page": 20,
}
_POLICY_GROUP = "group:policies"

ART_DIRECTION = {
    "": "Natural editorial photography that suits what the store sells, soft daylight, true-to-life colours",
    "friendly": "Warm, bright and inviting lifestyle photography, soft daylight, cheerful but natural colours",
    "premium": "Refined, minimal still life with soft directional light, rich muted tones and a luxurious feel",
    "professional": "Clean, trustworthy commercial photography on a neutral backdrop, crisp even light",
    "playful": "Bright, colourful and energetic styled scene, bold but harmonious colours",
    "minimal": "Minimal, airy composition with lots of calm negative space and a soft neutral palette",
}

IMAGE_PROMPT = """Create a wide social media link-preview image (the picture shown when a web page is shared on WhatsApp, Facebook, LinkedIn or X) for one page of an Indian online store.

Store: {store}
What the store sells: {sells}
{about}
This page: {page}
{audience}
Art direction: {art}. One clear focal subject placed in the centre, with calm space around it so the picture still works when cropped to a 1.91:1 card. Realistic, high quality, uncluttered.

Strict rules:
- Absolutely no text, letters, numbers, words, logos, brand marks, labels, price tags, watermarks, signs or user-interface elements anywhere in the image.
- No recognisable real people, celebrities or trademarked products.
- Do not present an invented product as the store's own item: show the category, the materials, the setting and the mood instead.
- Safe for a general audience."""


# ---------------------------------------------------------------------------
# Which pages need an image
# ---------------------------------------------------------------------------
def _clean_url(value) -> str:
    return str(value or "").strip()


def logo_urls(content: dict) -> set[str]:
    """Image values that are the store's logo, not a share image."""
    out = set()
    for k in ("desktop_logo", "mobile_logo", "logo", "favicon"):
        v = _clean_url(content.get(k))
        if v:
            out.add(v)
    return out


def is_missing(value, logos: set[str]) -> bool:
    v = _clean_url(value)
    return not v or v in logos


def image_key(page: dict) -> str:
    """Pages that share one image share this key (all policies)."""
    return _POLICY_GROUP if page.get("type") == "policy page" else page["key"]


def plan(content: dict, pages: list[dict]) -> list[dict]:
    """The pages to draw, at most :data:`MAX_IMAGES` (policies counted once),
    most-shared first. Each item: ``{key, pages: [page keys], page}``."""
    content = content if isinstance(content, dict) else {}
    logos = logo_urls(content)
    saved = content.get("seo_pages") if isinstance(content.get("seo_pages"), dict) else {}
    brand = content.get("seo") if isinstance(content.get("seo"), dict) else {}

    wanted: dict[str, dict] = {}
    for page in pages:
        if page.get("type") not in _PRIORITY:   # product pages, unknown kinds
            continue
        own = (brand if page["key"] == "home" else saved.get(page["key"])) or {}
        if not isinstance(own, dict):
            own = {}
        # Set by the owner (or by an earlier run): kept, never redrawn.
        if not (is_missing(own.get("og_image"), logos) and is_missing(own.get("twitter_image"), logos)):
            continue
        # The page's own picture (product photo, cover, category image), or an
        # owner-set brand share image the page inherits.
        if not is_missing((page.get("auto") or {}).get("og_image"), logos):
            continue
        key = image_key(page)
        item = wanted.setdefault(key, {"key": key, "pages": [], "page": page})
        item["pages"].append(page["key"])
    ordered = sorted(wanted.values(), key=lambda i: (_PRIORITY.get(i["page"].get("type"), 50)))
    return ordered[:MAX_IMAGES]


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------
def _join(items, limit: int) -> str:
    return ", ".join(str(i) for i in (items or [])[:limit] if str(i).strip())


def _page_subject(item: dict) -> str:
    page = item["page"]
    facts = page.get("facts") or {}
    kind = page.get("type")
    label = page.get("label") or ""
    if item["key"] == _POLICY_GROUP:
        return ("the store's customer policies (privacy, returns, shipping, terms). Show care and trust: "
                "a neatly packed parcel, careful packaging details or a calm, orderly scene. Nothing legal-looking.")
    if kind == "home page":
        return (f"the store's home page. Show the overall range: {_join(facts.get('categories'), 6)}. "
                f"Example items: {_join(facts.get('example_products'), 5)}.")
    if kind == "about page":
        return f"the brand's About page, its story and craft: {str(facts.get('text') or '')[:350]}"
    if kind == "all products listing":
        return f"the full product range: {_join(facts.get('categories'), 8)}."
    if kind == "category index":
        return f"browsing by category: {_join(facts.get('categories'), 8)}."
    if kind == "blog index":
        return f"the store's blog of guides and stories: {_join(facts.get('posts'), 5)}."
    if kind == "guides index":
        return f"helpful guides and information: {_join(facts.get('pages'), 5)}."
    if kind == "contact page":
        return "getting in touch with the store: a welcoming, friendly customer-care feel, a tidy desk or a parcel ready to go."
    if kind == "certificates page":
        return f"quality and certification: {_join(facts.get('certificates'), 5)}. A careful quality-check mood."
    if kind == "blog post":
        return f'a blog article titled "{facts.get("title") or label}". {str(facts.get("excerpt") or facts.get("text") or "")[:300]}'
    if kind == "content page":
        return (f'an information page titled "{facts.get("title") or label}". '
                f'{facts.get("subtitle") or ""} {str(facts.get("text") or "")[:250]}')
    if kind == "category page":
        return (f'the "{facts.get("name") or label}" category. {str(facts.get("description") or "")[:250]} '
                f"Example items: {_join(facts.get('example_products'), 5)}.")
    return f'the page "{label}".'


def build_prompt(store: dict, brief: dict, item: dict) -> str:
    brief = brief or {}
    sells = _join(store.get("categories"), 10) or "a curated range of products"
    about = store.get("about") or store.get("description") or ""
    audience = []
    if brief.get("audience"):
        audience.append(f"Made for: {brief['audience']}.")
    if brief.get("location"):
        audience.append(f"Shoppers are in {brief['location']}; the setting should feel natural to them.")
    text = IMAGE_PROMPT.format(
        store=store.get("name") or "an online store",
        sells=sells,
        about=f"About the store: {str(about)[:400]}" if about else "",
        page=_page_subject(item),
        audience=" ".join(audience),
        art=ART_DIRECTION.get(brief.get("tone") or "", ART_DIRECTION[""]),
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:3800]


# ---------------------------------------------------------------------------
# The API call and the card
# ---------------------------------------------------------------------------
def available(creds=None) -> bool:
    return bool(creds.api_key if creds is not None else getattr(settings, "OPENAI_API_KEY", ""))


def _request(prompt: str, creds=None) -> bytes:
    """Raw image bytes from the OpenAI image API (``creds``: the app's key
    and image model, marketplace.ai.Credentials; else the server settings)."""
    model = creds.image() if creds is not None else (getattr(settings, "OPENAI_IMAGE_MODEL", "") or "gpt-image-1")
    api_key = creds.api_key if creds is not None else settings.OPENAI_API_KEY
    base_url = creds.base_url if creds is not None else settings.OPENAI_BASE_URL
    body = {"model": model, "prompt": prompt, "n": 1}
    if model.startswith("dall-e"):
        body.update({"size": "1792x1024", "response_format": "b64_json"})
    else:
        body.update({"size": "1536x1024", "quality": QUALITY})
    url = f"{base_url.rstrip('/')}/images/generations"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    item = (out.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=TIMEOUT) as resp:
            return resp.read()
    raise RuntimeError("No image was returned.")


def to_card(raw: bytes) -> bytes:
    """Centre-crop to 1.91:1, scale to 1200 x 628, encode as a JPEG (the
    format every link unfurler reads)."""
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        w, h = im.size
        target = CARD_SIZE[0] / CARD_SIZE[1]
        if w / h > target:
            nw = int(h * target)
            left = (w - nw) // 2
            im = im.crop((left, 0, left + nw, h))
        else:
            nh = int(w / target)
            top = (h - nh) // 2
            im = im.crop((0, top, w, top + nh))
        im = im.resize(CARD_SIZE, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return buf.getvalue()


def _error_text(exc) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = (json.loads(exc.read().decode("utf-8")).get("error") or {}).get("message") or ""
        except Exception:
            detail = ""
        return f"OpenAI HTTP {exc.code}: {detail}".strip(": ")[:500]
    return f"{type(exc).__name__}: {exc}"[:500] if exc else ""


def _draw(prompt: str, deadline: float, creds=None) -> tuple[bytes | None, str]:
    """One card, retried once: ``(jpeg bytes, '')`` or ``(None, why)``. Runs
    in a worker thread: no DB access."""
    last = None
    for attempt in range(2):
        if time.monotonic() > deadline:
            return None, "Skipped: the run reached its time budget"
        try:
            return to_card(_request(prompt, creds)), ""
        except urllib.error.HTTPError as exc:
            last = exc
            if 400 <= exc.code < 500 and exc.code != 429:
                break   # refused (safety system, bad request): retrying won't help
        except Exception as exc:
            last = exc
        if attempt == 0:
            time.sleep(3)
    logger.warning("magic seo image failed: %s", last)
    return None, _error_text(last) or "No image was returned"


def _save(site, item: dict, data: bytes) -> str:
    from django.core.files.storage import default_storage

    label = "policies" if item["key"] == _POLICY_GROUP else (item["page"].get("label") or "page")
    stem = slugify(label)[:40] or "page"
    path = default_storage.save(f"sites/{site.pk}/seo/{stem}-{secrets.token_hex(4)}.jpg", ContentFile(data))
    return default_storage.url(path)


# ---------------------------------------------------------------------------
# One run's images
# ---------------------------------------------------------------------------
def generate(run, store: dict, pages: list[dict], started: float, creds=None) -> dict[str, str]:
    """Draw the share images a run needs. Returns ``{page key: image url}``.
    Progress goes to the run's ``images_*`` fields. Never raises."""
    from .models import MagicSeoRun

    site = run.site
    qs = MagicSeoRun.objects.for_site(site).filter(pk=run.pk)
    try:
        site.refresh_from_db(fields=["content"])
        items = plan(site.content or {}, pages)
    except Exception:
        logger.warning("magic seo image plan failed for site %s", site.pk, exc_info=True)
        return {}
    if not items or not available(creds):
        return {}

    qs.update(images_total=len(items), images_done=0, images_made=0, updated_at=timezone.now())
    _log(run, "images.started", f"Drawing {len(items)} share images for pages that have none",
         pages=[i["page"].get("label", i["key"]) for i in items][:20])
    deadline = started + RUN_BUDGET_SECONDS
    brief = run.brief or {}
    out: dict[str, str] = {}
    done = made = 0
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(items))) as pool:
        futures = {pool.submit(_draw, build_prompt(store, brief, item), deadline, creds): item for item in items}
        for fut in as_completed(futures):
            item = futures[fut]
            done += 1
            label = "All policy pages" if item["key"] == _POLICY_GROUP else item["page"].get("label", item["key"])
            try:
                data, why = fut.result()
                if data:
                    url = _save(site, item, data)
                    for key in item["pages"]:
                        out[key] = url
                    made += 1
                    _log(run, "image.made", f"Share image created for {label}", url=url, pages=len(item["pages"]))
                else:
                    _log(run, "image.failed", f"No share image for {label}: {why}", level="warning")
            except Exception as exc:
                logger.warning("magic seo image save failed for site %s", site.pk, exc_info=True)
                _log(run, "image.failed", f"Could not save the share image for {label}", level="warning",
                     error=_error_text(exc))
            qs.update(images_done=done, images_made=made, updated_at=timezone.now())
    _log(run, "images.done", f"{made} of {len(items)} share images created",
         level="info" if made == len(items) else "warning", planned=len(items), made=made,
         model=creds.image() if creds is not None else getattr(settings, "OPENAI_IMAGE_MODEL", ""))
    return out


def _log(run, event, message, level="info", **context):
    from .magic_seo import _log as log

    log(run, event, message, level=level, **context)
