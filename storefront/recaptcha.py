"""Google reCAPTCHA v2 ("I'm not a robot" checkbox) for storefront contact forms.

The store owner pastes the two keys from their own Google reCAPTCHA admin
console (https://www.google.com/recaptcha/admin, type "Challenge (v2)" >
"I'm not a robot" Checkbox, with the store's domain added) in Edit Content >
Contact. They live on :class:`tenancy.models.Site` (``recaptcha_site_key`` in
the clear, ``recaptcha_secret_key`` encrypted).

When both keys are set:

* every design's contact page renders the checkbox widget (``site_key`` in
  the page, ``api.js`` loaded from Google);
* the storefront CSP admits Google's reCAPTCHA script and frame hosts for that
  store only (``core.middleware``, :data:`CSP_SCRIPT` / :data:`CSP_FRAME`);
* :func:`verify` checks the widget's token with Google's ``siteverify`` API
  before :func:`storefront.views.enquiry` saves a contact-page submission.

When either key is missing nothing changes: no widget, no Google request.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

__all__ = [
    "CSP_FRAME", "CSP_SCRIPT", "V3_ACTION", "V3_MIN_SCORE",
    "enabled", "site_key", "verify", "version", "VERIFY_URL",
]

#: v3 (score-based) keys: the action every storefront enquiry form reports,
#: and the lowest score accepted (Google's own suggested default).
V3_ACTION = "enquiry"
V3_MIN_SCORE = 0.5

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

#: Hosts the widget needs, admitted to the storefront CSP only for a store
#: that has captcha on. api.js comes from www.google.com and loads its code
#: from www.gstatic.com; the checkbox and challenge render in google.com frames.
CSP_SCRIPT = ("https://www.google.com/recaptcha/", "https://www.gstatic.com/recaptcha/")
CSP_FRAME = ("https://www.google.com/recaptcha/", "https://recaptcha.google.com/recaptcha/")

#: Seconds to wait for Google's siteverify API.
_TIMEOUT = 6


def enabled(site) -> bool:
    """True when this store has both reCAPTCHA keys set."""
    return bool(
        site is not None
        and (getattr(site, "recaptcha_site_key", "") or "").strip()
        and (getattr(site, "recaptcha_secret_key", "") or "").strip()
    )


def version(site) -> str:
    """"v3" for score-based keys, else "v2" (the "I'm not a robot" checkbox).

    Chosen by the owner in Edit Content > Contact > Key type
    (``content["recaptcha_version"]``). It must match the key's type in
    Google's console; Google shows "Invalid key type" in the widget otherwise.
    """
    content = getattr(site, "content", None) or {}
    return "v3" if str(content.get("recaptcha_version") or "").strip().lower() == "v3" else "v2"


def site_key(site) -> str:
    """The public site key to render, or "" when captcha is off."""
    return (site.recaptcha_site_key or "").strip() if enabled(site) else ""


def verify(site, token: str, remote_ip: str = "") -> tuple[bool, str]:
    """Check a widget token with Google. Returns ``(ok, message_for_shopper)``.

    A missing token means the shopper did not tick the box. If Google cannot
    be reached the submission is refused (fail closed) with a "try again"
    message, so a captcha the owner turned on is never silently skipped.
    """
    v3 = version(site) == "v3"
    token = (token or "").strip()
    if not token:
        if v3:
            return False, "We could not check the captcha. Please reload the page and try again."
        return False, "Please tick the “I'm not a robot” box before sending."
    data = {"secret": (site.recaptcha_secret_key or "").strip(), "response": token}
    if remote_ip and remote_ip != "unknown":
        data["remoteip"] = remote_ip
    try:
        resp = requests.post(VERIFY_URL, data=data, timeout=_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError):
        logger.warning("reCAPTCHA verify request failed for site %s", getattr(site, "pk", "?"), exc_info=True)
        return False, "We could not check the captcha just now. Please try again."
    if payload.get("success"):
        if v3:
            # Score-based: 1.0 is very likely a person, 0.0 a bot. The action
            # is checked too, so a token minted for another purpose is refused.
            try:
                score = float(payload.get("score"))
            except (TypeError, ValueError):
                score = 0.0
            if (payload.get("action") or V3_ACTION) != V3_ACTION:
                return False, "Captcha check failed. Please try again."
            if score < V3_MIN_SCORE:
                return False, (
                    "We could not confirm this message was sent by a person. "
                    "Please try again, or contact the store another way."
                )
        return True, ""
    codes = payload.get("error-codes") or []
    if any(c in ("invalid-input-secret", "missing-input-secret") for c in codes):
        # The owner's secret key is wrong: say so in the log for support; the
        # shopper only sees a generic message.
        logger.warning("reCAPTCHA secret key rejected by Google for site %s: %s", getattr(site, "pk", "?"), codes)
        return False, "This form's captcha is not set up correctly. Please contact the store another way."
    if "timeout-or-duplicate" in codes:
        if v3:
            return False, "The captcha expired. Please send again."
        return False, "The captcha expired. Please tick the box again and resend."
    if v3:
        return False, "Captcha check failed. Please try again."
    return False, "Captcha check failed. Please tick the box again and resend."
