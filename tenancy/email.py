"""A store's outbound email identity.

Every email a store sends to *its own customers*  order updates, store credit,
owner notices, storefront sign-in codes  goes out from a single branded address
on one shared, SES-verified sending domain: the store's name merged into one
token, ``@via.onewooz.com``. So "Jack & Jones" mails its shoppers from
``jackandjones@via.onewooz.com``, and an owner running five stores sends from
five different addresses without configuring anything.

The transformation, applied to the store name:

* ``&`` becomes the word ``and``  it is read and written that way, so
  "Jack & Jones" and "Jack and Jones" both land on ``jackandjones``, never
  ``jackjones``.
* every word is then merged: spaces and any character that is not a plain
  ``a-z0-9`` are dropped, leaving one lowercase token.

The address part is the branded one; the From header additionally carries the
store's name as its display name, so a shopper's inbox shows "Jack & Jones",
not "jackandjones". Sending this class of mail through Amazon SES is what makes
the shared domain work  see :mod:`core.mail`.
"""

from __future__ import annotations

import re

from django.conf import settings

__all__ = [
    "store_email_local_part",
    "store_from_address",
    "store_from_header",
]

_DEFAULT_EMAIL_DOMAIN = "via.onewooz.website"

# Anything that is not an unadorned local-part character. Applied after "&" has
# been spelled out, so what survives the substitution is a-z0-9 concatenated.
_NON_LOCAL = re.compile(r"[^a-z0-9]+")


def store_email_local_part(name: str) -> str:
    """Merge a store name into the local part of its customer sender address.

    "Jack & Jones" and "Jack and Jones" → ``jackandjones``; "Nike" → ``nike``.
    Returns "" when the name has no usable characters (e.g. it was only symbols),
    which lets the caller fall back to the store slug.
    """
    text = (name or "").strip().lower()
    text = text.replace("&", " and ")
    return _NON_LOCAL.sub("", text)


def store_from_address(site) -> str:
    """The bare branded sender address for ``site``, e.g.
    ``jackandjones@via.onewooz.com``.

    Falls back to the site slug, then to ``store``, so the address is always
    well-formed even for a name that merges to nothing.
    """
    domain = getattr(settings, "STOREFRONT_EMAIL_DOMAIN", _DEFAULT_EMAIL_DOMAIN)
    local = (
        store_email_local_part(getattr(site, "name", ""))
        or store_email_local_part(getattr(site, "slug", ""))
        or "store"
    )
    return f"{local}@{domain}"


def store_from_header(site) -> str:
    """The full ``From`` header for ``site``: the store name as display name in
    front of the branded address, RFC 5322-encoded.

    Uses :func:`email.utils.formataddr` so a name containing a comma, quotes or
    other special characters is quoted correctly and never corrupts the header.
    """
    from email.utils import formataddr

    address = store_from_address(site)
    display = (getattr(site, "name", "") or "").strip()
    if not display:
        return address
    try:
        return formataddr((display, address))
    except Exception:
        # A display name that cannot be encoded must never block the send; the
        # bare branded address is a correct, if plainer, From.
        return address
