"""The dashboard AI assistant  text (xAI Grok) and image (OpenAI) generation.

Both providers are called **server-side** with keys read from settings (env),
so no key reaches the browser and no third-party host needs to be added to the
CSP. Text uses xAI's OpenAI-compatible chat-completions endpoint; images use
OpenAI's image API, returned to the page as a ``data:`` URL (already allowed by
``img-src``).

Every function returns a plain dict the view serialises to JSON. Network and
provider errors are caught and returned as ``{"ok": False, "error": …}`` so the
assistant degrades gracefully instead of 500-ing the dashboard.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 60  # seconds  image generation in particular can be slow.

_SYSTEM_PROMPT = (
    "You are the OneWooz assistant, helping a store owner run their online store. "
    "Be concise, practical and friendly. Help with product descriptions, marketing "
    "copy, pricing ideas, SEO, and general store operations. Use Indian Rupees (₹) "
    "for money unless told otherwise."
)


def text_available() -> bool:
    return bool(getattr(settings, "XAI_API_KEY", ""))


def image_available() -> bool:
    return bool(getattr(settings, "OPENAI_API_KEY", ""))


def _post_json(url: str, api_key: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def generate_text(prompt: str) -> dict:
    """Answer ``prompt`` with xAI Grok. Returns ``{ok, text}`` or ``{ok:False,error}``."""
    if not text_available():
        return {"ok": False, "error": "Text generation isn't configured."}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Please type a message."}

    url = f"{settings.XAI_BASE_URL.rstrip('/')}/chat/completions"
    body = {
        "model": settings.XAI_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt[:6000]},
        ],
        "stream": False,
    }
    try:
        out = _post_json(url, settings.XAI_API_KEY, body)
        text = (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not text:
            return {"ok": False, "error": "The assistant returned an empty reply."}
        return {"ok": True, "text": text}
    except urllib.error.HTTPError as exc:
        logger.warning("Grok text error: %s", exc)
        return {"ok": False, "error": "The assistant is unavailable right now. Please try again."}
    except Exception:
        logger.exception("Grok text generation failed")
        return {"ok": False, "error": "Something went wrong generating a reply."}


def generate_image(prompt: str) -> dict:
    """Generate an image with OpenAI. Returns ``{ok, image}`` (a data: URL)."""
    if not image_available():
        return {"ok": False, "error": "Image generation isn't configured."}
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Describe the image you want."}

    url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/images/generations"
    body = {
        "model": settings.OPENAI_IMAGE_MODEL,
        "prompt": prompt[:4000],
        "size": "1024x1024",
        "n": 1,
    }
    try:
        out = _post_json(url, settings.OPENAI_API_KEY, body)
        item = (out.get("data") or [{}])[0]
        b64 = item.get("b64_json")
        if b64:
            return {"ok": True, "image": f"data:image/png;base64,{b64}"}
        # Some models return a URL instead of base64.
        remote = item.get("url")
        if remote:
            return {"ok": True, "image": remote}
        return {"ok": False, "error": "No image was returned."}
    except urllib.error.HTTPError as exc:
        logger.warning("OpenAI image error: %s", exc)
        return {"ok": False, "error": "Image generation is unavailable right now. Please try again."}
    except Exception:
        logger.exception("OpenAI image generation failed")
        return {"ok": False, "error": "Something went wrong generating the image."}
