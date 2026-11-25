"""Render a storefront widget to HTML. Runs at **compile** time only.

Values are merged as: placement options (staff) <- owner values (only keys the
app defines as settings fields). Every value is cleaned for its role (text,
colour, https URL, identifier, allowlisted HTML) before it reaches a template,
and the templates autoescape on top of that.
"""

from __future__ import annotations

import secrets

from django.template.loader import render_to_string

from . import blocks
from .sanitize import clean_color, clean_html, clean_https_url, clean_identifier, clean_text

MAX_FRAGMENT = 64 * 1024

_COLOR_KEYS = {"bg_color", "text_color"}
_URL_KEYS = {"link_url", "url", "button_url"}
_RICH_KEYS = {"content", "html"}
_IMAGE_PREFIX = "image"
_BOOL_KEYS = {"dismissible", "show_once"}


class WidgetError(Exception):
    """The widget cannot render with these values (missing required, bad option)."""


def _clean_value(key: str, value):
    if key in _COLOR_KEYS:
        return clean_color(value)
    if key in _URL_KEYS:
        return clean_https_url(value)
    if key.startswith(_IMAGE_PREFIX):
        return clean_https_url(value, allow_http=True)
    if key in _RICH_KEYS:
        return clean_html(value)
    if key in _BOOL_KEYS:
        return value in (True, "1", "true", "on", 1)
    if key == "tracking_id":
        return clean_identifier(value)
    if key == "delay_seconds":
        try:
            return max(0, min(120, int(value)))
        except (TypeError, ValueError):
            return 5
    if key == "max_width":
        try:
            return str(max(240, min(1600, int(str(value).strip().rstrip("px")))))
        except (TypeError, ValueError):
            return ""
    if key == "align":
        return "center" if str(value).strip().lower() == "center" else "left"
    if key == "kind":
        v = str(value or "").strip().lower()
        return v if v in ("whatsapp", "call", "link") else "link"
    if key == "phone":
        return "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")[:20]
    if key.startswith("a_") or key in ("text", "message"):
        return clean_text(value, 1000)
    return clean_text(value, 255)


def merged_values(placement, owner_values: dict, field_keys: set[str]) -> dict:
    widget = blocks.WIDGET_TYPES.get(placement.widget_type)
    keys = set(widget.fields if widget else ())
    out: dict = {}
    for k, v in (placement.options or {}).items():
        if k in keys:
            out[k] = v
    for k, v in (owner_values or {}).items():
        if k in keys and k in field_keys and v not in (None, ""):
            out[k] = v
    return {k: _clean_value(k, v) for k, v in out.items()}


def render(*, site, app, placement, owner_values: dict, field_keys: set[str]) -> tuple[str, list[str]]:
    """Return ``(html, script_origins)`` or raise :class:`WidgetError`."""
    widget = blocks.WIDGET_TYPES.get(placement.widget_type)
    if widget is None:
        raise WidgetError(f"unknown widget type {placement.widget_type!r}")

    if widget.key == "module_widget":
        from . import modules

        module = modules.get(app.module)
        if module is None:
            raise WidgetError("module_widget needs a module on the app")
        html = clean_html(module.render_widget(site=site, app=app, placement=placement, values=owner_values or {}),
                          max_length=MAX_FRAGMENT)
        return html, []

    values = merged_values(placement, owner_values, field_keys)
    missing = [k for k in widget.required if not values.get(k)]
    if missing:
        raise WidgetError(f"missing required value(s): {', '.join(missing)}")

    origins: list[str] = []
    ctx = {"w": values, "uid": "ow" + secrets.token_hex(4), "app": app.slug, "site_name": getattr(site, "name", "")}

    if widget.key == "pixel":
        opts = placement.options or {}
        ident = values.get("tracking_id", "")
        src_tpl = str(opts.get("src") or "")
        allowed = app.script_origins()
        src = src_tpl.replace("{tracking_id}", ident) if src_tpl else ""
        if src:
            if not src.startswith("https://") or not any(src == o or src.startswith(o + "/") for o in allowed):
                raise WidgetError("pixel src is not on an origin staff approved for this app")
            origin = "/".join(src.split("/", 3)[:3])
            origins.append(origin)
        inline = str(opts.get("inline") or "")[:4000].replace("{tracking_id}", ident)
        if "</script" in inline.lower():
            raise WidgetError("inline pixel code may not contain </script")
        ctx.update({"src": src, "inline": inline})
        origins.extend(o for o in allowed if o not in origins)

    if widget.key == "faq_block":
        ctx["pairs"] = [(values.get(f"q_{i}"), values.get(f"a_{i}")) for i in range(1, 6) if values.get(f"q_{i}")]
    if widget.key == "image_strip":
        ctx["images"] = [values[k] for k in ("image_1", "image_2", "image_3", "image_4", "image_5", "image_6") if values.get(k)]
    if widget.key == "trust_strip":
        ctx["items"] = [values[k] for k in ("item_1", "item_2", "item_3", "item_4") if values.get(k)]
    if widget.key == "floating_button":
        kind = values.get("kind") or "link"
        href = ""
        if kind == "whatsapp" and values.get("phone"):
            from urllib.parse import quote

            href = f"https://wa.me/{values['phone'].lstrip('+')}" + (
                f"?text={quote(values.get('message', ''))}" if values.get("message") else ""
            )
        elif kind == "call" and values.get("phone"):
            href = f"tel:{values['phone']}"
        else:
            href = values.get("url", "")
        if not href:
            raise WidgetError("floating button needs a phone number or link")
        ctx["href"] = href
        ctx["kind"] = kind

    html = render_to_string(f"marketplace/widgets/{widget.key}.html", ctx).strip()
    if len(html.encode("utf-8")) > MAX_FRAGMENT:
        raise WidgetError("widget output is larger than 64 KB")
    return html, origins
