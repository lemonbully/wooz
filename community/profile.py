"""A member's optional profile photo, website link and brand name.

Asked on the Join form (all optional) and editable any time from their
profile. All three show next to their name on every question and answer.

Security, field by field:

* **Photo.** Size-capped before decoding, decoded by Pillow (PNG, JPEG or
  WEBP only; GIF, TIFF, SVG and everything else is refused), pixel count
  capped against decompression bombs, then re-encoded from pixels to a fresh
  256 px square WebP. The original bytes are never stored or served, so
  metadata (EXIF location), polyglot files and embedded scripts are dropped.
  Stored under a random name.
* **Website.** http(s) only; no credentials, IP addresses, localhost or
  internal names; ASCII host names only (no look-alike Unicode domains);
  checked against the community's blocked keywords. Rendered through
  ``Member.safe_website`` with ``rel="nofollow ugc noopener noreferrer"``.
* **Brand name.** Plain text: control characters, markup and links refused,
  blocked keywords refused, 60 characters. Django escapes it on output.

Saving is rate-limited (``ask.profile.save``) and every form is CSRF
protected. Banned members' websites and brand names are never shown.
"""

from __future__ import annotations

import io
import ipaddress
import re
import secrets
import unicodedata
from urllib.parse import urlsplit

from django import forms
from django.core.files.base import ContentFile

from .models import Member

MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 40_000_000
AVATAR_SIZE = 256
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")
BRAND_MAX = 60

_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp",
                          ".invalid", ".test", ".example", ".onion", ".arpa")
_HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _blocked_term(text: str) -> str:
    """The first community blocked keyword/phrase in ``text``, or ""."""
    from . import services

    try:
        conf = services.settings_now()
        hit = services.blocked_term_in(text, [*((w, "phrase") for w in services._SPAM_WORDS), *conf.blocked_terms])
    except Exception:
        return ""
    return hit[0] if hit else ""


def clean_brand(raw: str) -> str:
    """A safe one-line brand name, or raise ValidationError."""
    text = unicodedata.normalize("NFKC", raw or "")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") for ch in text if ch not in " \t"):
        raise forms.ValidationError("Use plain letters, numbers and punctuation for your brand name.")
    text = " ".join(text.split())
    if not text:
        return ""
    if len(text) > BRAND_MAX:
        raise forms.ValidationError(f"Keep your brand name to {BRAND_MAX} characters.")
    if re.search(r"[<>{}\[\]\\`|@]", text):
        raise forms.ValidationError("Your brand name can't contain < > { } [ ] \\ ` | or @.")
    from . import services

    if services._URL_RE.search(text):
        raise forms.ValidationError("Put your web address in the Website box, not in the brand name.")
    if _blocked_term(text):
        raise forms.ValidationError("Choose a different brand name.")
    return text


def clean_website(raw: str) -> str:
    """A safe public http(s) address, or raise ValidationError."""
    url = (raw or "").strip()
    if not url:
        return ""
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise forms.ValidationError("Enter a website address without spaces, like yourbrand.com.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise forms.ValidationError("Enter a website address, like yourbrand.com.")
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise forms.ValidationError("Enter a website address, like yourbrand.com.")
    if parts.username or parts.password:
        raise forms.ValidationError("Enter the plain website address, without a username or password.")
    if port not in (None, 80, 443):
        raise forms.ValidationError("Enter the plain website address, without a port number.")
    try:
        ipaddress.ip_address(host.strip("[]"))
        raise forms.ValidationError("Enter your website's name, not an IP address.")
    except ValueError:
        pass
    if not host.isascii():
        raise forms.ValidationError("Enter the website address in plain English letters (punycode for other scripts).")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES) or not _HOST_RE.match(host):
        raise forms.ValidationError("Enter a public website address, like yourbrand.com.")
    if _blocked_term(url):
        raise forms.ValidationError("That website can't be shown here.")
    return url


def _open_checked(upload):
    """The upload decoded by Pillow, after the format and size checks."""
    from PIL import Image

    if getattr(upload, "size", 0) > MAX_AVATAR_BYTES:
        raise forms.ValidationError("Choose a photo of 5 MB or less.")
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    # Cheap magic-byte check before any decoding.
    if not (head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
        raise forms.ValidationError("Choose a PNG, JPG or WEBP photo.")
    try:
        img = Image.open(upload)  # reads the header only; size is known before decoding
    except Exception:
        raise forms.ValidationError("That file isn't a photo we can use. Try a PNG, JPG or WEBP.")
    if img.format not in ALLOWED_FORMATS:
        raise forms.ValidationError("Choose a PNG, JPG or WEBP photo.")
    width, height = img.size
    if not width or not height or width * height > MAX_AVATAR_PIXELS or max(width, height) > 12000:
        raise forms.ValidationError("That photo is too large. Try one under 6000 × 6000 pixels.")
    return img


def process_avatar(upload) -> bytes:
    """The upload as a fresh AVATAR_SIZE square WebP. Raises ValidationError."""
    from PIL import Image, ImageOps

    img = _open_checked(upload)
    try:
        with img:
            img.draft("RGB", (AVATAR_SIZE * 2, AVATAR_SIZE * 2))  # JPEG: decode smaller, faster
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P", "PA") else "RGB")
            img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "WEBP", quality=82, method=6)  # no exif/icc passed: metadata dropped
            return out.getvalue()
    except forms.ValidationError:
        raise
    except Exception:
        raise forms.ValidationError("That file isn't a photo we can use. Try a PNG, JPG or WEBP.")


class ProfileExtrasForm(forms.Form):
    avatar = forms.FileField(
        label="Profile photo", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
    )
    remove_avatar = forms.BooleanField(label="Remove my photo", required=False)
    brand_name = forms.CharField(label="Brand name", required=False, max_length=BRAND_MAX * 2)
    website = forms.CharField(label="Website", required=False, max_length=300)

    def clean_avatar(self):
        upload = self.cleaned_data.get("avatar")
        if not upload:
            return None
        # Decoded and re-encoded here, once; save() stores these bytes.
        self.avatar_webp = process_avatar(upload)
        return upload

    def clean_brand_name(self):
        return clean_brand(self.cleaned_data.get("brand_name", ""))

    def clean_website(self):
        url = (self.cleaned_data.get("website") or "").strip()
        if url and "://" not in url:
            url = "https://" + url
        return clean_website(url)

    def error_text(self) -> str:
        return " ".join(e for errs in self.errors.values() for e in errs)


def _delete_file(name: str) -> None:
    if not name:
        return
    try:
        Member._meta.get_field("avatar").storage.delete(name)
    except Exception:
        pass  # a stray old file is harmless; never fail the save over it


def store_avatar(member: Member, webp: bytes) -> None:
    """Save already-processed WebP bytes as the member's photo, replacing any old one."""
    old = member.avatar.name if member.avatar else ""
    member.avatar.save(f"{member.pk.hex[:12]}-{secrets.token_hex(8)}.webp", ContentFile(webp), save=False)
    Member.objects.filter(pk=member.pk).update(avatar=member.avatar.name)
    if old and old != member.avatar.name:
        _delete_file(old)


def save(member: Member, form: ProfileExtrasForm) -> None:
    """Apply a valid form to the member (profile edit)."""
    data = form.cleaned_data
    changes = {}
    if member.website != data.get("website", ""):
        changes["website"] = data.get("website", "")
    if member.brand_name != data.get("brand_name", ""):
        changes["brand_name"] = data.get("brand_name", "")
    if changes:
        Member.objects.filter(pk=member.pk).update(**changes)
        for key, value in changes.items():
            setattr(member, key, value)
    webp = getattr(form, "avatar_webp", None)
    if webp:
        store_avatar(member, webp)
    elif data.get("remove_avatar") and member.avatar:
        old = member.avatar.name
        Member.objects.filter(pk=member.pk).update(avatar="")
        member.avatar = ""
        _delete_file(old)


# ---------------------------------------------------------------------------
# Join: the extras are picked before the email is confirmed, so they wait
# (text in the session, the processed photo in the cache under a random key)
# and are applied only after the code is verified.
# ---------------------------------------------------------------------------
JOIN_PHOTO_TTL = 30 * 60


def hold_for_join(form: ProfileExtrasForm) -> dict:
    """What to keep in the session until the code is confirmed."""
    from django.core.cache import cache

    held = {"website": form.cleaned_data.get("website", ""), "brand_name": form.cleaned_data.get("brand_name", "")}
    webp = getattr(form, "avatar_webp", None)
    if webp:
        key = secrets.token_urlsafe(24)
        cache.set(f"ask:joinphoto:{key}", webp, JOIN_PHOTO_TTL)
        held["photo"] = key
    return held


def apply_after_join(member: Member, held: dict) -> None:
    """Fill in what the member chose on the Join form. Only blank fields are
    filled, so signing back in never overwrites a profile set up earlier."""
    from django.core.cache import cache

    held = held or {}
    changes = {}
    for field in ("website", "brand_name"):
        value = held.get(field) or ""
        if value and not getattr(member, field):
            changes[field] = value
    if changes:
        Member.objects.filter(pk=member.pk).update(**changes)
        for key, value in changes.items():
            setattr(member, key, value)
    key = held.get("photo") or ""
    if key and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", key):
        webp = cache.get(f"ask:joinphoto:{key}")
        cache.delete(f"ask:joinphoto:{key}")
        if isinstance(webp, bytes) and webp[:4] == b"RIFF" and not member.avatar:
            store_avatar(member, webp)
