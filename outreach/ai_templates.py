"""ChatGPT-backed email-template generation for the admin template builder.

Called server-side with the OpenAI key from settings, so no key reaches the
browser and no CSP host is opened. The builder is conversational: each turn
sends the running chat history plus the current HTML and asks ChatGPT to return
the *complete* updated template. The model is told about the pre-defined
``{{variables}}`` the store fills at send time, so the templates it writes stay
compatible with :mod:`outreach.services.rendering`.

Returns a plain dict the view serialises to JSON; network/provider errors are
caught and returned as ``{"ok": False, "error": …}`` so the builder degrades
gracefully instead of 500-ing.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from .variables import variables_prompt_reference

logger = logging.getLogger(__name__)

_TIMEOUT = 90

_SYSTEM_PROMPT = (
    "You are an expert email-template designer for an online-store marketing platform. "
    "You produce ONE complete, self-contained HTML email document (<!DOCTYPE html> … "
    "</html>) using TABLE-based layout and INLINE CSS only  no <style> blocks, no "
    "external stylesheets, web-safe fonts, squared corners  so it renders across Gmail, "
    "Outlook, Apple Mail and Yahoo.\n\n"
    "The store fills these placeholders at send time; use them verbatim where natural, "
    "and NEVER invent other placeholders:\n"
    f"{variables_prompt_reference()}\n\n"
    "Rules:\n"
    "1. The call-to-action anchor's href MUST be exactly {{cta_url}}.\n"
    "2. Include an unsubscribe link in the footer whose href is exactly {{unsubscribe_url}}.\n"
    "3. Put {{products}} where a product showcase belongs (it is replaced with a ready-made "
    "product grid)  use it at most once.\n"
    "4. Use {{store_name}}, {{store_logo}} (an <img src>), {{store_email}}, {{positioning}} "
    "and {{recipient_name}} where they fit.\n"
    "5. Return ONLY the raw HTML for the whole email  no markdown fences, no commentary.\n"
    "6. When asked to revise, return the FULL updated document again, not a fragment."
)

# ── Prompt orchestration ─────────────────────────────────────────────────────
# Before the admin's brief reaches the model, we prepend a professional,
# type-specific directive and an enhancement instruction, so a one-line brief
# still yields a fully-designed, on-brand template of the right kind. There are
# exactly three email fabrics: single-product, multi-product and promotional.

TYPE_DIRECTIVES = {
    "single": (
        "EMAIL TYPE  SINGLE-PRODUCT SPOTLIGHT.\n"
        "Build the whole email around ONE product: a large hero product image, a strong "
        "product-focused headline, 2–4 crisp benefit lines, clear price/offer emphasis, and "
        "a single prominent {{cta_url}} button. Use {{products}} once for the product block. "
        "Keep it laser-focused  one product, one action, generous whitespace."
    ),
    "multi": (
        "EMAIL TYPE  MULTI-PRODUCT SHOWCASE.\n"
        "Build a tidy catalogue-style email featuring several products: a short intro, then "
        "{{products}} as a clean, scannable grid (up to four), each reading as a shoppable card, "
        "with a primary {{cta_url}} button and a subtle browse-all link. Balanced, gift-guide feel."
    ),
    "promotional": (
        "EMAIL TYPE  PROMOTIONAL / OFFER.\n"
        "Build a bold, high-energy promotional email centred on an OFFER (sale, discount, launch, "
        "seasonal event): an attention-grabbing hero with the offer headline and its value "
        "(discount %, coupon code, deadline/urgency), punchy supporting copy, an optional {{products}} "
        "teaser, and a high-contrast {{cta_url}} button. Lead with the offer; make value and urgency "
        "unmistakable while staying tasteful and on-brand."
    ),
}

_ENHANCE_PREAMBLE = (
    "\n\nHOW TO USE THE BRIEF:\n"
    "The user's message is a SHORT brief, not final copy. Silently ENHANCE it into a complete, "
    "professional design brief for this exact email type  decide section order, visual hierarchy, "
    "a tasteful accent palette, spacing, button placement, an engaging headline and supporting copy, "
    "and imagery placement  then output the finished email. Even a one-line brief must become a "
    "polished, conversion-focused, on-brand template. Make confident design decisions; never ask "
    "the user questions back."
)


# ── Design-style orchestration ───────────────────────────────────────────────
# The admin picks a visual STYLE (Minimalism, Cyberpunk, Editorial …). We inject
# a concrete, email-safe directive for it, so a one-line brief already yields a
# template that *looks* like that style  palette, typography, layout, decoration
#  without the admin describing the look each time. Every directive is written
# to survive email clients: table layout, inline CSS, web-safe fonts only, no
# <style>, no external assets. Effects a client can't do (real gradients, blur,
# custom fonts) are approximated with solid fallbacks, borders, spacing, tracked
# uppercase caps and the store's own imagery. Keys are the slugs the builder sends.

_STYLE_PREAMBLE = (
    "DESIGN STYLE  render the ENTIRE email in this visual language. It OVERRIDES the "
    "default look (e.g. the plain 'squared corners / restrained accent' baseline) wherever "
    "they conflict, but it NEVER overrides the email-client-safety rules: table-based layout, "
    "inline CSS only, no <style> blocks, no external stylesheets or web fonts, and web-safe "
    "font stacks. Approximate anything a mail client can't do (real gradients, blur, custom "
    "fonts) with a SOLID fallback colour plus the effect layered on top, bold tracked uppercase "
    "type, borders, generous spacing and the store's own images. Commit fully to the style while "
    "keeping every placeholder rule above intact.\n\n"
)

STYLE_DIRECTIVES = {
    "minimalism": (
        "STYLE  MINIMALISM.\n"
        "Vast whitespace on a near-white paper background (#ffffff / #f5f4f2). One restrained accent "
        "(a single deep colour) used only for the CTA and a hairline or two  nothing else coloured. "
        "Type: a clean sans stack (Helvetica, Arial, sans-serif); one calm headline in a large light/"
        "regular weight, small tracked uppercase labels (letter-spacing ~2px) for eyebrows. Thin 1px "
        "hairline rules divide sections; squared corners; generous 32–48px padding; a single column, "
        "centred, lots of air. The product/showcase reads as quiet, uncluttered cards. The CTA is a "
        "simple solid rectangle. Elegant, editorial calm  say less, space more."
    ),
    "maximalism": (
        "STYLE  MAXIMALISM.\n"
        "More is more: loud, layered, joyful. Saturated clashing colour blocks (hot pink, electric blue, "
        "acid yellow, purple) stacked as full-bleed table rows; a huge oversized headline in heavy bold "
        "caps, mixed type sizes and colours within copy, sticker-like framed callouts and bordered badges. "
        "Backgrounds alternate boldly between sections; use thick borders and high contrast. Buttons are big, "
        "chunky, high-saturation with a bold border. Energetic, expressive, unapologetic  pack the layout "
        "densely but keep it readable and the placeholders intact."
    ),
    "futuristic": (
        "STYLE  FUTURISTIC.\n"
        "Sleek near-future tech. Dark charcoal/near-black background (#0b0d10) with a single vivid accent "
        "(electric red #ff2d2d or cyan). Condensed BOLD UPPERCASE headlines with tight spacing; tiny "
        "monospace-style status labels in tracked caps (e.g. 'SYSTEM ACTIVE', 'EST. 2089', 'DIGITAL EDITION') "
        "as thin-ruled meta rows. Split-panel/asymmetric layout, thin accent hairlines, a HUD/dashboard feel. "
        "Light text on dark; the CTA is a crisp high-contrast accent bar. Cinematic, precise, forward-looking."
    ),
    "vector-art": (
        "STYLE  VECTOR / FLAT ILLUSTRATION.\n"
        "Flat, clean, geometric. A muted-but-confident palette of 3–4 solid fills (dusty plum, slate blue, "
        "warm tan, cream) laid down as smooth colour blocks  no textures, no shadows-for-depth, crisp edges. "
        "Rounded-but-simple shapes, a friendly geometric sans (Verdana/Trebuchet MS/Arial), medium weights. "
        "Treat section backgrounds as illustrated colour fields; frame product images cleanly. Soft "
        "8–12px rounded corners. Modern, illustrative, approachable  like a flat-design poster."
    ),
    "collage-art": (
        "STYLE  COLLAGE / CUT-PAPER.\n"
        "A hand-made mixed-media scrapbook. Layer overlapping rectangular 'scraps' (bordered coloured boxes, "
        "off-white paper panels) at the section level; mix a serif (Georgia) with a sans within the same view; "
        "add handwritten-feel notes as italic asides and small taped-label badges. Muted print palette with a "
        "few bright pops, torn/ragged feel evoked with thick uneven borders and slight overlaps (via nested "
        "tables and borders, not real rotation). Eclectic, tactile, editorial-zine energy  keep it legible."
    ),
    "retro": (
        "STYLE  RETRO / VINTAGE (70s).\n"
        "Warm nostalgic groove. Palette of mustard, burnt orange, avocado green, cream and deep brown. "
        "Big rounded chunky headline caps, a warm serif or rounded sans for body; pill-shaped buttons; "
        "thick rounded frames and sunburst/stripe feels built from bordered rows. Cream paper background, "
        "earthy colour blocks. Friendly, sun-faded, analog warmth  like a vintage catalogue."
    ),
    "cyberpunk": (
        "STYLE  CYBERPUNK.\n"
        "Neon-noir. Deep near-black background with glitchy neon accents  magenta #ff007a, cyan #00eaff, "
        "acid green. Bold techno UPPERCASE headlines, monospace-style tracked labels, faux terminal/console "
        "lines ('> EXECUTING…', '_2K.045_FETCHING') as tiny mono meta. High contrast, thin neon hairlines, "
        "scanline/grid feel via bordered rows. Buttons glow with a bright accent fill on dark. Gritty, "
        "high-energy, dystopian-tech  light text on dark throughout."
    ),
    "pop-art": (
        "STYLE  POP ART.\n"
        "Comic-book punch. Primary colours (red, yellow, cyan) plus black outlines; a bursting 'POW!'-style "
        "hero headline in heavy italic caps; thick 2–3px black borders around blocks and buttons; halftone/"
        "dot energy suggested with bold colour and outlined shapes. High saturation, high contrast, playful. "
        "Buttons are bold with a heavy black outline. Loud, fun, Lichtenstein/Warhol comic-strip vibe."
    ),
    "glassmorphism": (
        "STYLE  GLASS MORPHISM.\n"
        "Modern frosted-glass elegance. A soft colourful backdrop (a tasteful gradient with a SOLID fallback "
        "colour) behind translucent-looking 'cards'  light panels with subtle 1px light borders, soft rounded "
        "16px corners, and a faint inner highlight to read as frosted glass (approximated with pale semi-tint "
        "backgrounds since real blur is unavailable). Airy, premium, minimal copy, a clean modern sans. "
        "The CTA is a soft rounded pill. Sleek, contemporary SaaS-brand modernity."
    ),
    "clay": (
        "STYLE  CLAY / 3D SOFT.\n"
        "Playful soft-3D. Pastel palette (mint, peach, soft coral, sky) on a light background; big rounded "
        "'puffy' shapes and cards with very round 20–28px corners; soft, friendly rounded sans; large soft "
        "buttons that look pillowy (rounded pill, gentle accent fill). Bouncy, tactile, cheerful  like "
        "soft-clay 3D render illustration. Keep plenty of rounded, cushioned whitespace."
    ),
    "pixel-art": (
        "STYLE  PIXEL ART / 8-BIT.\n"
        "Retro video-game. A limited blocky palette (a handful of flat colours), Courier New / monospace for "
        "a pixel-terminal feel, hard SQUARED corners everywhere (never rounded), chunky blocky borders and "
        "'health-bar' style dividers. Headlines in bold uppercase monospace. Backgrounds are flat blocks of "
        "colour. Buttons are hard-edged rectangles with a thick border. Nostalgic arcade energy  everything "
        "reads as low-res, gridded and deliberate."
    ),
    "editorial": (
        "STYLE  EDITORIAL / MAGAZINE.\n"
        "High-fashion magazine cover. A commanding oversized serif display headline (Georgia / 'Times New "
        "Roman', tight leading) is the hero; refined black-and-white base with ONE accent (often deep red). "
        "Small tracked uppercase kickers ('A VISUAL STORY', 'ISSUE 01'), thin column rules, justified/measured "
        "body copy, ample margins. Squared corners, restrained but confident. The CTA is understated and "
        "elegant. Sophisticated, print-editorial, Vogue/Kinfolk poise."
    ),
    "y2k": (
        "STYLE  Y2K.\n"
        "Early-2000s tech-optimism. Shiny chrome/metallic feel, bubbly rounded type, glossy gradients "
        "(with solid fallbacks), electric blue, hot pink and silver; star/sparkle accents (✦ ✧ ★) and "
        "rounded 'bubble' buttons. Playful, glossy, futuristic-nostalgic. Backgrounds cool metallic or "
        "bright; keep it fun and slightly maximal without losing the placeholders."
    ),
    "swiss": (
        "STYLE  SWISS / INTERNATIONAL TYPOGRAPHIC.\n"
        "Objective grid design. Strict alignment on an implied grid, Helvetica/Arial only, flush-left ragged-"
        "right copy, a large bold headline, dramatic scale contrast, and one accent (classic red #e2231a). "
        "White background, black text, generous margins, thin rules; asymmetric but rigorously ordered; "
        "squared corners. No decoration for its own sake  clarity, hierarchy and whitespace do the work. "
        "The CTA is a clean solid rectangle. Timeless, precise, functional."
    ),
    "surreal": (
        "STYLE  SURREAL / DREAMLIKE.\n"
        "Poetic and unexpected. Soft dreamy gradients (with solid fallbacks) in twilight hues  lilac, peach, "
        "deep indigo  floating/off-kilter composition, airy oversized type mixed with delicate small caps, "
        "and evocative, almost cryptic copy. Generous negative space, gentle rounded corners, a luminous feel. "
        "The CTA glows softly. Ethereal, imaginative, a little mysterious  like a dream you half-remember."
    ),
    "bohemian": (
        "STYLE  BOHEMIAN / ORGANIC.\n"
        "Warm, earthy, hand-crafted. Terracotta, sand, olive, ochre and cream; arch/organic shapes suggested "
        "with rounded tops; a warm serif for headings with relaxed body copy; natural, unhurried spacing. "
        "Soft rounded corners, muted natural palette, artisanal feel. Buttons are warm and rounded. Free-"
        "spirited, natural, boutique-and-crafts warmth."
    ),
    "victorian": (
        "STYLE  VICTORIAN / ORNATE.\n"
        "Antique and decorative. Deep rich tones (burgundy, forest green, navy) with gold/cream accents; "
        "classic serif ('Times New Roman', Georgia) with elegant tracked small caps; ornate framed borders "
        "(double-rule frames via nested bordered tables), decorative divider flourishes (❦ ✦ centred rules), "
        "and a formal, refined tone. Squared framed panels, symmetrical, stately. The CTA is a bordered "
        "gold-accented button. Heritage, luxury, old-world craftsmanship."
    ),
    "graffiti": (
        "STYLE  GRAFFITI / STREET.\n"
        "Urban and raw. High-contrast street palette  spray-can bright colours over dark concrete-grey "
        "backgrounds; bold slanted/heavy display caps for the hero, drippy energetic feel evoked with thick "
        "outlines and colour splashes (bordered blocks), tag-style badges. Gritty, rebellious, high-energy. "
        "Buttons are bold, outlined, spray-bright. Keep the copy punchy and the layout dynamic but legible."
    ),
    "aurora": (
        "STYLE  AURORA.\n"
        "Luminous night-sky glow. A deep dark background (#0a0a12) with a flowing aurora gradient hero  "
        "teal, magenta, violet and soft gold blended (with a solid dark fallback)  glowing softly against "
        "the dark. Light, airy type; a delicate large headline; small tracked uppercase labels; lots of dark "
        "negative space so the colour glow sings. The CTA is a luminous accent pill. Ethereal, premium, "
        "atmospheric  light text on a dark, glowing canvas."
    ),
}

# Ordered (slug, label) list for the builder's style picker.
STYLE_CHOICES = [
    ("minimalism", "Minimalism"),
    ("maximalism", "Maximalism"),
    ("futuristic", "Futuristic"),
    ("vector-art", "Vector art"),
    ("collage-art", "Collage art"),
    ("retro", "Retro"),
    ("cyberpunk", "Cyberpunk"),
    ("pop-art", "Pop art"),
    ("glassmorphism", "Glass morphism"),
    ("clay", "Clay style"),
    ("pixel-art", "Pixel art"),
    ("editorial", "Editorial"),
    ("y2k", "Y2K"),
    ("swiss", "Swiss design"),
    ("surreal", "Surreal"),
    ("bohemian", "Bohemian"),
    ("victorian", "Victorian"),
    ("graffiti", "Graffiti"),
    ("aurora", "Aurora"),
]


_MAX_IMAGES = 4
_MAX_IMAGE_CHARS = 8_000_000  # ~6 MB per base64 image, generous but bounded
_MAX_FILES = 4
_MAX_FILE_CHARS = 8_000


def _clean_images(images) -> list:
    """Keep only well-formed base64 image data URLs, capped in count and size."""
    out = []
    for url in (images or []):
        if not isinstance(url, str):
            continue
        if not url.startswith("data:image/"):
            continue
        if len(url) > _MAX_IMAGE_CHARS:
            continue
        out.append(url)
        if len(out) >= _MAX_IMAGES:
            break
    return out


def _clean_files(files) -> list:
    """Keep only small text/markup reference files ``{name, text}``."""
    out = []
    for f in (files or []):
        if not isinstance(f, dict):
            continue
        text = (f.get("text") or "").strip()
        if not text:
            continue
        name = (f.get("name") or "file").strip()[:120]
        out.append({"name": name, "text": text[:_MAX_FILE_CHARS]})
        if len(out) >= _MAX_FILES:
            break
    return out


def _system_prompt_for(template_type: str, style: str = "") -> str:
    """Compose system + email-type directive + design-style directive + enhancer.

    The style directive is the prompt-orchestration layer: picking a style
    pre-applies its full look so a short brief still yields an on-style template.
    """
    parts = [_SYSTEM_PROMPT]
    directive = TYPE_DIRECTIVES.get((template_type or "").strip().lower())
    if directive:
        parts.append(directive)
    style_directive = STYLE_DIRECTIVES.get((style or "").strip().lower())
    if style_directive:
        parts.append(_STYLE_PREAMBLE + style_directive)
    return "\n\n".join(parts) + _ENHANCE_PREAMBLE


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Best-effort provider error message from a failed OpenAI call.

    The error body can only be read once, so callers extract the message here
    and reuse the returned string.
    """
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return ((payload.get("error") or {}).get("message") or "").strip()
    except Exception:
        return ""


def _post_json(url: str, api_key: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def available() -> bool:
    return bool(getattr(settings, "OPENAI_API_KEY", ""))


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def generate(
    prompt: str,
    *,
    history=None,
    current_html: str = "",
    model: str = "",
    template_type: str = "",
    style: str = "",
    images=None,
    files=None,
) -> dict:
    """Return ``{ok, html, model}`` for a builder turn, or ``{ok:False, error}``.

    ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``
    from earlier turns; ``current_html`` is the template as it stands, so the
    model revises rather than starting over. ``model`` optionally overrides
    ``settings.OPENAI_TEXT_MODEL`` for this call  the builder passes the model
    code the admin typed; blank falls back to the configured default.
    ``template_type`` (``single`` | ``multi`` | ``promotional``) selects the
    professional type-specific directive that enhances the brief. ``style`` (a
    slug from :data:`STYLE_CHOICES`, e.g. ``editorial``, ``cyberpunk``) pre-applies
    that visual style's full directive so the brief need not describe the look.

    ``images`` is a list of ``data:image/...;base64,...`` URLs (reference images
    the admin attached  brand look, a mock, a logo) sent to the vision model.
    ``files`` is a list of ``{"name", "text"}`` (small text/markup files 
    brand notes, copy, an SVG logo) folded into the brief as context.
    """
    if not available():
        return {"ok": False, "error": "The template builder isn’t configured (no OpenAI key)."}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Type what you’d like to change."}

    messages = [{"role": "system", "content": _system_prompt_for(template_type, style)}]
    for turn in (history or [])[-12:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content[:8000]})
    if current_html.strip():
        messages.append(
            {
                "role": "user",
                "content": "The current template HTML is:\n\n" + current_html[:20000],
            }
        )

    # Fold any attached text/markup files into the brief as reference context.
    user_text = prompt[:6000]
    for f in _clean_files(files):
        user_text += f"\n\n--- Reference file: {f['name']} ---\n{f['text']}"

    # Attached images go to the vision model as image parts alongside the text.
    clean_images = _clean_images(images)
    if clean_images:
        user_text += (
            "\n\n(Reference image(s) attached above  match their look, colours and "
            "layout intent where it makes sense; do not embed them, use {{store_logo}}/"
            "{{products}} for real assets.)"
        )
        content_parts = [{"type": "text", "text": user_text}]
        for url in clean_images:
            content_parts.append({"type": "image_url", "image_url": {"url": url}})
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": user_text})

    # A typed model code wins; otherwise the configured default. Cap the length
    # so a stray paste can't build an absurd request.
    chosen_model = (model or "").strip()[:120] or settings.OPENAI_TEXT_MODEL

    url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    body = {
        "model": chosen_model,
        "messages": messages,
        "temperature": 0.6,
    }
    try:
        try:
            out = _post_json(url, settings.OPENAI_API_KEY, body)
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            # Some models only accept the default temperature and reject any
            # explicit value. Drop the parameter and retry once so the request
            # still goes through instead of failing.
            if "temperature" in detail.lower() and "temperature" in body:
                body.pop("temperature", None)
                out = _post_json(url, settings.OPENAI_API_KEY, body)
            else:
                # Surface the provider's message (admin-only screen) so a
                # mistyped or unavailable model code reads as a clear error.
                logger.warning("OpenAI template builder HTTP error: %s", exc)
                if detail:
                    return {"ok": False, "error": f"Model “{chosen_model}”: {detail}"}
                return {"ok": False, "error": "The builder is unavailable right now. Please try again."}
        text = (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        html = _strip_fences(text)
        if not html:
            return {"ok": False, "error": "The builder returned an empty template."}
        return {"ok": True, "html": html, "model": chosen_model}
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        logger.warning("OpenAI template builder HTTP error: %s", exc)
        if detail:
            return {"ok": False, "error": f"Model “{chosen_model}”: {detail}"}
        return {"ok": False, "error": "The builder is unavailable right now. Please try again."}
    except Exception:
        logger.exception("OpenAI template builder failed")
        return {"ok": False, "error": "Something went wrong generating the template."}
