"""Google Merchant Center integration (Merchant API v1).

How it fits together
--------------------
* **Platform** (one-time, by OneWooz): one Google Cloud project with the
  Merchant API and the Site Verification API enabled, one OAuth "Web
  application" client (``settings.GOOGLE_OAUTH_CLIENT_ID`` / ``_SECRET``), and
  that project registered *once* with OneWooz's own Merchant Center account
  (``developerRegistration:registerGcp``). Google's guidance for third-party
  platforms is to register with your own primary account, never with each
  merchant's: :func:`register_platform_project` (an admin action) does it.

* **Owner** (one click): :func:`authorization_url` sends them to Google's
  consent screen asking for Merchant Center (``auth/content``) and Site
  Verification access, offline (a refresh token). :func:`complete_authorization`
  stores the encrypted refresh token, lists the Merchant Center accounts the
  owner can reach and, when there is exactly one, :func:`finish_connection`
  wires it up: an API-input primary data source, the store's website set as the
  account homepage, verified (a ``google-site-verification`` meta tag served by
  the storefront) and claimed, then a first full product sync.

* **Sync**: every product becomes one offer, or one offer per variation sharing
  an ``itemGroupId`` (:func:`build_offers`). :func:`sync_product` upserts the
  offers with ``productInputs:insert`` and deletes offers that no longer exist
  (a removed variation, a deactivated or deleted product). Saving a product
  queues it (``commerce.apps`` signals), a full sync can be started from the
  dashboard, and Celery Beat re-pushes every auto-sync catalogue every few hours
  (stock changed by orders, and Google expires offers not refreshed in 30 days).
  :func:`refresh_statuses` reads the processed products back
  (``productStatus``) so the owner sees approved / pending / disapproved and the
  exact issues Google reports.

Every function here is safe to call from a request or a worker; network
failures surface as :class:`MerchantError`, never as a crashed page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger("onewooz.merchant")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://merchantapi.googleapis.com"
SITE_VERIFICATION_API = "https://www.googleapis.com/siteVerification/v1"

SCOPE_CONTENT = "https://www.googleapis.com/auth/content"
SCOPE_SITE_VERIFICATION = "https://www.googleapis.com/auth/siteverification"
# ``openid email`` only identifies which Google account connected (shown on the
# page); Merchant Center access is the content scope.
SCOPES = ("openid", "email", SCOPE_CONTENT, SCOPE_SITE_VERIFICATION)

STATE_PURPOSE = "merchant.oauth_state"
SESSION_NONCE_KEY = "gm_oauth_nonce"

TIMEOUT = 30
# Limits from the product data specification.
TITLE_MAX = 150
DESCRIPTION_MAX = 5000
OFFER_ID_MAX = 50
ADDITIONAL_IMAGES_MAX = 10
HIGHLIGHTS_MAX = 10
HIGHLIGHT_LEN = 150
PRODUCT_DETAILS_MAX = 50


class MerchantError(Exception):
    """A Merchant / OAuth API call failed. ``message`` is safe to show owners."""

    def __init__(self, message, *, status=None, reason="", fatal=False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason
        # A fatal error (revoked access, lost permission) stops a full sync
        # instead of failing every remaining product the same way.
        self.fatal = fatal


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def is_configured() -> bool:
    """True when the platform OAuth client is set (env)."""
    return bool(
        getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
        and getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    )


def redirect_uri(request) -> str:
    """The single OAuth redirect URI registered on the platform client."""
    from django.urls import reverse

    return request.build_absolute_uri(reverse("google_merchant_callback"))


def merchant_center_url(merchant_id: str, page: str = "products") -> str:
    """Deep link into the owner's Merchant Center."""
    paths = {
        "products": "mc/products/diagnostics",
        "overview": "mc/overview",
        "shipping": "mc/shipping/services",
        "business": "mc/businessinfo",
    }
    return f"https://merchants.google.com/{paths.get(page, page)}?a={merchant_id}"


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------
def authorization_url(request, site) -> str:
    """Google's consent screen URL for connecting ``site``.

    The ``state`` is a purpose-bound signed token naming the store and the user,
    plus a nonce also kept in this browser's session, so the callback can only
    complete for the same signed-in owner in the same browser that started it.
    """
    import secrets

    from core import tokens

    nonce = secrets.token_urlsafe(16)
    request.session[SESSION_NONCE_KEY] = nonce
    state = tokens.sign(
        STATE_PURPOSE, {"site": str(site.pk), "user": str(request.user.pk), "n": nonce}
    )
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # A refresh token, so syncs keep working without the owner present.
        "access_type": "offline",
        # Always show consent so Google always returns a refresh token (it only
        # sends one on the first consent otherwise).
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def read_state(request, state: str) -> dict:
    """Validate the callback ``state``; returns its payload or raises."""
    from core import tokens

    try:
        data = tokens.unsign(STATE_PURPOSE, state or "")
    except tokens.TokenError:
        raise MerchantError("This Google sign-in link has expired. Please click Connect again.")
    nonce = request.session.pop(SESSION_NONCE_KEY, None)
    if not nonce or data.get("n") != nonce or data.get("user") != str(request.user.pk):
        raise MerchantError("This Google sign-in didn't start in this browser. Please click Connect again.")
    return data


def _token_request(data: dict) -> dict:
    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise MerchantError("Couldn't reach Google. Please try again.") from exc
    body = _json(resp)
    if resp.status_code >= 400:
        err = str(body.get("error") or "")
        if err == "invalid_grant":
            raise MerchantError(
                "Google access was revoked or expired. Please reconnect your account.",
                status=resp.status_code, reason=err, fatal=True,
            )
        raise MerchantError(
            body.get("error_description") or "Google rejected the sign-in. Please try again.",
            status=resp.status_code, reason=err,
        )
    return body


def exchange_code(code: str, request) -> dict:
    """Trade the authorization code for tokens (access + refresh)."""
    return _token_request({
        "code": code,
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri(request),
        "grant_type": "authorization_code",
    })


def _email_from_id_token(id_token: str) -> str:
    """The ``email`` claim of an ID token received directly from Google's token
    endpoint over TLS (so its signature need not be re-checked here)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("email") or "")[:254]
    except Exception:
        return ""


def revoke(token: str) -> None:
    if not token:
        return
    try:
        requests.post(GOOGLE_REVOKE_URL, params={"token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        pass


def complete_authorization(request, config, code: str) -> list:
    """Store the tokens from the callback and return the reachable accounts.

    Raises :class:`MerchantError` when the owner didn't grant Merchant Center
    access. Listing accounts failing is not fatal: the owner can enter their
    Merchant Center ID by hand.
    """
    tokens_ = exchange_code(code, request)
    granted = str(tokens_.get("scope") or "")
    if SCOPE_CONTENT not in granted.split():
        revoke(tokens_.get("refresh_token") or tokens_.get("access_token") or "")
        raise MerchantError(
            "Google Merchant Center access wasn't granted. On the Google screen, tick "
            "“Manage your product listings and accounts for Google Shopping” and try again."
        )
    refresh = tokens_.get("refresh_token") or config.refresh_token
    if not refresh:
        raise MerchantError("Google didn't return offline access. Please click Connect again.")
    previous = config.refresh_token
    config.refresh_token = refresh
    config.access_token = tokens_.get("access_token") or ""
    config.access_token_expires_at = timezone.now() + timedelta(
        seconds=max(int(tokens_.get("expires_in") or 3600) - 120, 60)
    )
    config.granted_scopes = granted
    config.google_email = _email_from_id_token(tokens_.get("id_token") or "") or config.google_email
    config.connected_at = timezone.now()
    config.status = config.Status.PENDING
    config.last_error = ""
    config.save()
    if previous and previous != refresh:
        revoke(previous)
    try:
        return MerchantClient(config).list_accounts()
    except MerchantError as exc:
        if is_registration_error(exc) and try_platform_registration(config):
            # This sign-in was an admin of OneWooz's own account and just did
            # the one-time registration. Google can take a few minutes to
            # apply it, so a retry may still be refused.
            try:
                accounts = MerchantClient(config).list_accounts()
                config.last_error = ""
                config.save(update_fields=["last_error", "updated_at"])
                return accounts
            except MerchantError:
                exc = MerchantError(
                    "Google developer registration is done. Google takes up to 5 minutes to "
                    "apply it; please click “Connect with Google” again in a few minutes."
                )
        config.last_error = exc.message
        config.save(update_fields=["last_error", "updated_at"])
        return []


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------
def _json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


REGISTRATION_PENDING_MESSAGE = (
    "OneWooz's Google integration is waiting for its one-time developer registration. "
    "Our team has been notified; please try again shortly."
)


def _is_registration_text(message: str) -> bool:
    m = (message or "").lower()
    return "not registered" in m or "developer registration" in m or "registergcp" in m


def is_registration_error(exc) -> bool:
    """True when Google refused because OneWooz's Cloud project isn't yet
    registered with a Merchant Center account (a platform setup step, not
    anything the store owner did wrong)."""
    return isinstance(exc, MerchantError) and exc.message == REGISTRATION_PENDING_MESSAGE


def _alert_registration_pending(detail: str) -> None:
    """Tell the OneWooz team (error log + admin email), at most every 6 hours,
    that every merchant connection is blocked on the developer registration."""
    from django.core.cache import cache

    try:
        if not cache.add("gm:registration-alert", 1, 6 * 3600):
            return
    except Exception:
        pass
    platform_id = getattr(settings, "GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", "")
    text = (
        "Google Merchant connections are failing: the Cloud project behind the OAuth client "
        "is not registered with a Merchant Center account.\n\n"
        f"Google said: {detail}\n\n"
        "Fix (one time): "
        + (
            f"connect OneWooz's own Merchant Center account ({platform_id}) from any store's "
            "Google Merchant page with a Google login that is ADMIN on it; registration then "
            "runs automatically."
            if platform_id
            else "set ONEWOOZ_GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID to OneWooz's own Merchant "
            "Center ID and connect that account from any store's Google Merchant page with a "
            "Google login that is ADMIN on it (or use the admin action on Google Merchant configs)."
        )
    )
    logger.error("merchant developer registration missing: %s", detail)
    try:
        from django.core.mail import mail_admins

        mail_admins("Google Merchant: developer registration needed", text, fail_silently=True)
    except Exception:
        pass


def _friendly(status: int, message: str, reason: str) -> tuple[str, bool]:
    """An owner-facing message for an API error, and whether it is fatal."""
    if _is_registration_text(message):
        return (REGISTRATION_PENDING_MESSAGE, True)
    if status == 401:
        return ("Google access expired or was revoked. Please reconnect your account.", True)
    if status == 403:
        return (
            "This Google account doesn't have admin access to the Merchant Center account. "
            "Reconnect with an account that does.",
            True,
        )
    if status == 429:
        return ("Google is rate-limiting requests. The sync will retry automatically.", False)
    if status >= 500:
        return ("Google Merchant Center is temporarily unavailable. Please try again.", False)
    return (message or f"Google returned an error (HTTP {status}).", False)


class MerchantClient:
    """Authenticated calls for one store's connection."""

    def __init__(self, config):
        self.config = config

    # ---- tokens ----------------------------------------------------------
    def access_token(self) -> str:
        cfg = self.config
        if cfg.access_token and cfg.access_token_expires_at and cfg.access_token_expires_at > timezone.now():
            return cfg.access_token
        if not cfg.refresh_token:
            raise MerchantError("Google isn't connected. Please connect your account.", fatal=True)
        body = _token_request({
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "refresh_token": cfg.refresh_token,
            "grant_type": "refresh_token",
        })
        cfg.access_token = body.get("access_token") or ""
        cfg.access_token_expires_at = timezone.now() + timedelta(
            seconds=max(int(body.get("expires_in") or 3600) - 120, 60)
        )
        # Only these two columns are written (the field encrypts on save).
        cfg.save(update_fields=["access_token", "access_token_expires_at"])
        return cfg.access_token

    # ---- transport -------------------------------------------------------
    def request(self, method: str, url: str, *, params=None, body=None, attempts: int = 3) -> dict:
        """One API call. Retries 429/5xx briefly (workers only ever wait a few
        seconds in total); raises :class:`MerchantError` on failure."""
        last = None
        for attempt in range(attempts):
            headers = {"Authorization": f"Bearer {self.access_token()}"}
            try:
                resp = requests.request(
                    method, url, params=params, json=body, headers=headers, timeout=TIMEOUT
                )
            except requests.RequestException as exc:
                last = MerchantError("Couldn't reach Google. Please try again.")
                logger.warning("merchant api network error %s %s: %s", method, url, exc)
                time.sleep(1 + attempt)
                continue
            if resp.status_code == 401 and attempt == 0:
                # Token revoked/expired early: force a refresh once.
                self.config.access_token_expires_at = None
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            data = _json(resp)
            if resp.status_code >= 400:
                err = data.get("error") or {}
                raw = str(err.get("message") or "")
                reason = str(err.get("status") or "")
                msg, fatal = _friendly(resp.status_code, raw, reason)
                logger.info("merchant api %s %s -> %s %s", method, url, resp.status_code, raw[:500])
                if _is_registration_text(raw) and "registerGcp" not in url:
                    _alert_registration_pending(raw[:500])
                raise MerchantError(msg, status=resp.status_code, reason=reason or raw[:120], fatal=fatal)
            return data
        raise last or MerchantError("Google Merchant Center didn't respond. Please try again.")

    # ---- accounts --------------------------------------------------------
    def list_accounts(self) -> list:
        """Merchant Center accounts this Google account can access."""
        out, token = [], None
        for _ in range(20):
            params = {"pageSize": 250}
            if token:
                params["pageToken"] = token
            data = self.request("GET", f"{API}/accounts/v1/accounts", params=params)
            for a in data.get("accounts") or []:
                aid = str(a.get("accountId") or str(a.get("name") or "").rsplit("/", 1)[-1])
                if aid:
                    out.append({
                        "id": aid,
                        "name": a.get("accountName") or f"Account {aid}",
                        "test": bool(a.get("testAccount")),
                    })
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    def get_account(self, account_id: str) -> dict:
        return self.request("GET", f"{API}/accounts/v1/accounts/{account_id}")

    # ---- data source -----------------------------------------------------
    def ensure_data_source(self) -> str:
        """The API-input primary data source products are written to: the one
        already recorded, a matching existing one, or a newly created one."""
        cfg = self.config
        acct = cfg.merchant_id
        if cfg.data_source:
            try:
                self.request("GET", f"{API}/datasources/v1/{cfg.data_source}")
                return cfg.data_source
            except MerchantError as exc:
                if exc.status not in (404, 400):
                    raise
        feed, lang = cfg.effective_feed_label, cfg.content_language
        sources, token = [], None
        for _ in range(10):
            params = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            data = self.request("GET", f"{API}/datasources/v1/accounts/{acct}/dataSources", params=params)
            sources += data.get("dataSources") or []
            token = data.get("nextPageToken")
            if not token:
                break
        chosen = ""
        for src in sources:
            primary = src.get("primaryProductDataSource")
            if primary is None or str(src.get("input") or "").upper() != "API":
                continue
            s_feed = str(primary.get("feedLabel") or "").upper()
            s_lang = str(primary.get("contentLanguage") or "").lower()
            # Only an exact match, or a source that accepts any label/language.
            if (s_feed in ("", feed)) and (s_lang in ("", lang)):
                chosen = src.get("name") or ""
                if s_feed == feed and s_lang == lang:
                    break
        if not chosen:
            created = self.request(
                "POST",
                f"{API}/datasources/v1/accounts/{acct}/dataSources",
                body={
                    "displayName": f"OneWooz · {cfg.site.name}"[:100],
                    "primaryProductDataSource": {
                        "feedLabel": feed,
                        "contentLanguage": lang,
                        "countries": [cfg.default_country.upper() or feed],
                    },
                },
            )
            chosen = created.get("name") or ""
        if not chosen:
            raise MerchantError("Couldn't create a product data source in Merchant Center.")
        cfg.data_source = chosen
        cfg.feed_label = feed
        type(cfg).all_objects.filter(pk=cfg.pk).update(data_source=chosen, feed_label=feed)
        return chosen

    # ---- website ---------------------------------------------------------
    def ensure_website(self, url: str) -> None:
        """Set ``url`` as the account homepage, verify it with Site Verification
        (meta tag served by the storefront) and claim it. Best effort: failures
        are recorded on the config for the owner to retry, never raised."""
        cfg = self.config
        acct = cfg.merchant_id
        cfg.website_url = url
        cfg.website_error = ""
        try:
            home = {}
            try:
                home = self.request("GET", f"{API}/accounts/v1/accounts/{acct}/homepage")
            except MerchantError as exc:
                if exc.status not in (404,):
                    raise
            current = str(home.get("uri") or "").rstrip("/")
            if current and current != url.rstrip("/") and home.get("claimed"):
                # The account already has a different, claimed website: never
                # silently take it over (it may be the merchant's other shop).
                cfg.website_claimed = False
                cfg.website_error = (
                    f"Your Merchant Center account's website is {current}, but this store is {url}. "
                    "Google only approves products whose links match the claimed website: use a "
                    "Merchant Center account for this store, or change the website in Merchant "
                    "Center (Business info) and click “Verify & claim”."
                )
                return
            if current != url.rstrip("/"):
                # Changing the homepage drops any previous claim; only done when
                # the account points nowhere or at an unclaimed address.
                home = self.request(
                    "PATCH", f"{API}/accounts/v1/accounts/{acct}/homepage",
                    params={"updateMask": "uri"}, body={"uri": url},
                )
            if home.get("claimed"):
                cfg.website_verified = cfg.website_claimed = True
                return
            if SCOPE_SITE_VERIFICATION in (cfg.granted_scopes or "").split():
                self._verify_site(url)
            claim = self.request(
                "POST", f"{API}/accounts/v1/accounts/{acct}/homepage:claim", body={"overwrite": False}
            )
            cfg.website_claimed = bool(claim.get("claimed", True))
            cfg.website_verified = cfg.website_verified or cfg.website_claimed
        except MerchantError as exc:
            cfg.website_claimed = False
            cfg.website_error = (
                exc.message if exc.status not in (400, 412) and exc.reason != "FAILED_PRECONDITION"
                else "Google couldn't confirm you own this website yet. Make sure the store is "
                     "published and reachable, then click “Verify & claim” again."
            )
        finally:
            type(cfg).all_objects.filter(pk=cfg.pk).update(
                website_url=cfg.website_url,
                website_verified=cfg.website_verified,
                website_claimed=cfg.website_claimed,
                website_error=cfg.website_error,
                site_verification_token=cfg.site_verification_token,
            )

    def _verify_site(self, url: str) -> None:
        """Google Site Verification by meta tag: fetch a token, publish it in the
        storefront ``<head>`` (via ``site_verification_token``), then ask Google
        to verify. Verifying makes the connected Google account an owner of the
        site, which is what Merchant Center's claim checks."""
        cfg = self.config
        site_id = {"type": "SITE", "identifier": url.rstrip("/") + "/"}
        tok = self.request(
            "POST", f"{SITE_VERIFICATION_API}/token",
            body={"site": site_id, "verificationMethod": "META"},
        )
        raw = str(tok.get("token") or "")
        m = re.search(r'content="([^"]+)"', raw)
        cfg.site_verification_token = (m.group(1) if m else raw)[:255]
        type(cfg).all_objects.filter(pk=cfg.pk).update(site_verification_token=cfg.site_verification_token)
        try:
            self.request(
                "POST", f"{SITE_VERIFICATION_API}/webResource",
                params={"verificationMethod": "META"}, body={"site": site_id},
            )
            cfg.website_verified = True
        except MerchantError:
            # Not reachable yet (unpublished store, DNS still propagating). The
            # claim below will report it; the tag stays in place for a retry.
            cfg.website_verified = False

    # ---- products --------------------------------------------------------
    def insert_offer(self, payload: dict) -> dict:
        cfg = self.config
        return self.request(
            "POST",
            f"{API}/products/v1/accounts/{cfg.merchant_id}/productInputs:insert",
            params={"dataSource": cfg.data_source},
            body=payload,
        )

    def delete_offer(self, offer_id: str, *, language=None, feed_label=None, data_source=None) -> None:
        """Delete one offer (by default under the current language / feed label /
        data source; pass the old ones to clean up after a target change)."""
        cfg = self.config
        lang = (language or cfg.content_language).lower()
        feed = (feed_label or cfg.effective_feed_label).upper()
        name = f"{lang}~{feed}~{offer_id}"
        try:
            self.request(
                "DELETE",
                f"{API}/products/v1/accounts/{cfg.merchant_id}/productInputs/{quote(name, safe='')}",
                params={"dataSource": data_source or cfg.data_source},
            )
        except MerchantError as exc:
            if exc.status != 404:  # already gone is fine
                raise

    def list_products(self):
        """Yield processed products (with ``productStatus``)."""
        token = None
        for _ in range(200):
            params = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            data = self.request(
                "GET", f"{API}/products/v1/accounts/{self.config.merchant_id}/products", params=params
            )
            yield from data.get("products") or []
            token = data.get("nextPageToken")
            if not token:
                break


# --------------------------------------------------------------------------
# Connecting an account
# --------------------------------------------------------------------------
def store_public_base(site) -> str:
    """The store's public origin: its verified custom domain, else its
    OneWooz subdomain. Feed links and the claimed website use this."""
    from core import links

    if getattr(site, "custom_domain", "") and getattr(site, "custom_domain_verified", False):
        return f"https://{site.custom_domain.strip().lower().rstrip('/')}"
    return links.storefront_origin(site.slug).rstrip("/")


def finish_connection(config, account_id: str) -> None:
    """Attach the chosen Merchant Center account and get it ready to receive
    products. Raises :class:`MerchantError` if the account can't be used."""
    account_id = re.sub(r"\D", "", str(account_id or ""))
    if not account_id:
        raise MerchantError("Enter your numeric Merchant Center ID.")
    config.merchant_id = account_id
    client = MerchantClient(config)
    try:
        info = client.get_account(account_id)  # proves access
    except MerchantError as exc:
        if not (is_registration_error(exc) and try_platform_registration(config)):
            raise
        try:
            info = client.get_account(account_id)
        except MerchantError:
            raise MerchantError(
                "Google developer registration is done. Google takes up to 5 minutes to apply "
                "it; please click “Connect this account” again in a few minutes."
            ) from exc
    config.account_name = str(info.get("accountName") or "")[:255]
    if not config.default_language or config.default_language == "en":
        lang = str(info.get("languageCode") or "en").split("-", 1)[0].lower()
        config.default_language = lang or "en"
    config.status = config.Status.CONNECTED
    config.last_error = ""
    config.data_source = ""
    config.save()
    client.ensure_data_source()
    client.ensure_website(store_public_base(config.site))


# --------------------------------------------------------------------------
# Product → offers
# --------------------------------------------------------------------------
_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_COLOR_NAMES = {"color", "colour", "colors", "colours", "shade"}
_SIZE_NAMES = {"size", "sizes"}
_MATERIAL_NAMES = {"material", "fabric"}
_PATTERN_NAMES = {"pattern", "print"}
_GENDER_NAMES = {"gender"}
_AGE_NAMES = {"age group", "age"}
_GTIN_NAMES = {"gtin", "ean", "upc", "barcode", "isbn", "jan", "gtin-13", "ean-13"}
_MPN_NAMES = {"mpn", "manufacturer part number", "model number", "part number"}


def _norm_name(s) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def offer_id_for(product, combo=None) -> str:
    """A stable offer id: the product's id, plus a short hash of the variation's
    key. Never the SKU, so renaming a SKU doesn't orphan the Google listing."""
    base = product.pk.hex if hasattr(product.pk, "hex") else _ID_RE.sub("", str(product.pk))
    if combo is None:
        return base[:OFFER_ID_MAX]
    key = str(combo.get("key") or json.dumps(combo.get("attributes") or [], sort_keys=True))
    return f"{base[:32]}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def _money(value) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d > 0 else None


def _price_obj(amount: Decimal, currency: str) -> dict:
    micros = int((amount * Decimal(1_000_000)).quantize(Decimal(1)))
    return {"amountMicros": str(micros), "currencyCode": currency}


def _abs_url(base: str, url: str) -> str:
    v = str(url or "").strip()
    if not v or v.lower().startswith("data:"):
        return ""
    if v.lower().startswith(("http://", "https://")):
        return v
    if v.startswith("//"):
        return "https:" + v
    if not v.startswith("/"):
        media = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
        if media.lower().startswith(("http://", "https://")):
            return media.rstrip("/") + "/" + v.lstrip("/")
        v = "/" + media.strip("/") + "/" + v.lstrip("/")
    return base.rstrip("/") + v


def _product_images(product, base: str) -> list:
    urls = []
    try:
        for img in product.images.for_site(product.site).order_by("order"):
            try:
                u = _abs_url(base, img.image.url)
            except Exception:
                u = ""
            if u and u not in urls:
                urls.append(u)
    except Exception:
        pass
    return urls


def _category_path(product) -> str:
    cat = getattr(product, "category", None)
    parts = []
    seen = 0
    while cat is not None and seen < 5:
        parts.insert(0, cat.name)
        cat = getattr(cat, "parent", None)
        seen += 1
    return " > ".join(p for p in parts if p)


def _measure(value, unit, *, kind: str) -> dict | None:
    """A dimension/weight object in units Google accepts."""
    v = _money(value)
    if v is None:
        return None
    unit = (unit or "").lower()
    if kind == "length":
        if unit == "mm":
            v, unit = v / 10, "cm"
        if unit not in ("cm", "in"):
            unit = "cm"
    else:
        if unit not in ("kg", "g", "lb", "oz"):
            unit = "kg"
    return {"value": float(round(v, 3)), "unit": unit}


def _spec_lookup(product, names: set) -> str:
    for row in product.specifications or []:
        if isinstance(row, dict) and _norm_name(row.get("name")) in names:
            val = str(row.get("value") or "").strip()
            if val:
                return val
    return ""


def _gender(value: str) -> str:
    v = _norm_name(value)
    if v in ("male", "men", "man", "boys", "boy", "mens", "men's"):
        return "MALE"
    if v in ("female", "women", "woman", "girls", "girl", "womens", "women's", "ladies"):
        return "FEMALE"
    if v in ("unisex", "all"):
        return "UNISEX"
    return ""


def _age_group(value: str) -> str:
    v = _norm_name(value)
    return {
        "newborn": "NEWBORN", "infant": "INFANT", "toddler": "TODDLER",
        "kids": "KIDS", "kid": "KIDS", "children": "KIDS", "adult": "ADULT", "adults": "ADULT",
    }.get(v, "")


def _with_tax(amount: Decimal, product) -> Decimal:
    """Indian listings must show tax-inclusive prices; add GST when the
    product's price excludes it (matching what checkout charges)."""
    gst = _money(getattr(product, "gst_percentage", None))
    if gst and not getattr(product, "gst_inclusive", True):
        amount = amount * (Decimal(100) + gst) / Decimal(100)
    return amount.quantize(Decimal("0.01"))


def build_offers(product, config, site=None) -> tuple[list, list]:
    """``(offers, problems)``: the ProductInput payloads for ``product`` (one,
    or one per variation) and any reasons it can't be listed.

    Attribute mapping follows the product data specification: title, rich
    description (plain text), landing page, images, tax-inclusive price with the
    MRP as ``price`` and the selling price as ``salePrice`` when discounted,
    availability from stock, brand + identifiers (GTIN/MPN from the product's
    specifications, else ``identifierExists: false``), product type from the
    category path, highlights from the bullet points, product details from the
    specifications, dimensions and weights, and variation attributes
    (colour / size / material / pattern / gender / age group) grouped by
    ``itemGroupId``.
    """
    from commerce.cart import attrs_of, combos_of

    site = site or product.site
    base = store_public_base(site)
    problems = []
    currency = (getattr(product, "currency", "") or "INR").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        currency = "INR"

    title = " ".join(str(product.title or "").split())
    description = " ".join(strip_tags(product.description or "").split()) or title
    slug = getattr(product, "slug", "") or ""
    link = f"{base}/products/{quote(slug)}/" if slug else ""
    images = _product_images(product, base)
    details = product.d2c_details or {}

    brand = (product.brand or "").strip() or (site.name or "").strip()
    gtin = re.sub(r"\D", "", _spec_lookup(product, _GTIN_NAMES))
    mpn = _spec_lookup(product, _MPN_NAMES)

    common = {
        "description": description[:DESCRIPTION_MAX],
        "condition": "NEW",
        "adult": False,
    }
    if brand:
        common["brand"] = brand[:70]
    if gtin and 8 <= len(gtin) <= 14:
        common["gtins"] = [gtin]
    if mpn:
        common["mpn"] = mpn[:70]
    common["identifierExists"] = bool(common.get("gtins") or (mpn and brand))
    ptype = _category_path(product)
    if ptype:
        common["productTypes"] = [ptype[:750]]
    highlights = [
        " ".join(str(b).split())[:HIGHLIGHT_LEN]
        for b in (details.get("bullets") or []) if str(b).strip()
    ][:HIGHLIGHTS_MAX]
    if len(highlights) >= 2:  # Google wants 2-100 highlights
        common["productHighlights"] = highlights
    specs = []
    for row in product.specifications or []:
        if not isinstance(row, dict):
            continue
        n, v = str(row.get("name") or "").strip(), str(row.get("value") or "").strip()
        if n and v and _norm_name(n) not in (_GTIN_NAMES | _MPN_NAMES):
            specs.append({"sectionName": "General", "attributeName": n[:140], "attributeValue": v[:1000]})
    if specs:
        common["productDetails"] = specs[:PRODUCT_DETAILS_MAX]
    for key, unit_field, out in (
        ("product_length", "product_unit", "productLength"),
        ("product_breadth", "product_unit", "productWidth"),
        ("product_height", "product_unit", "productHeight"),
        ("package_length", "package_unit", "shippingLength"),
        ("package_breadth", "package_unit", "shippingWidth"),
        ("package_height", "package_unit", "shippingHeight"),
    ):
        m = _measure(getattr(product, key, None), getattr(product, unit_field, ""), kind="length")
        if m:
            common[out] = m
    weight = _measure(product.package_weight_value, product.package_weight_unit, kind="weight") or _measure(
        product.weight_value, product.weight_unit, kind="weight"
    )
    if weight:
        common["shippingWeight"] = weight
    if details.get("material"):
        common["material"] = str(details["material"])[:200]
    if details.get("colour"):
        common["color"] = str(details["colour"])[:100]
    if product.category_id and getattr(product, "category", None):
        common["customLabel0"] = product.category.name[:100]
    if product.is_best_seller:
        common["customLabel1"] = "best seller"

    def finish(offer_id, attrs, *, price, mrp, stock, img_list, link_url, variant_attrs=None):
        # Google rejects an offer without these, so it isn't sent at all; the
        # owner sees exactly what to add ("Missing details").
        p = _money(price)
        missing = []
        if not title:
            missing.append("Add a product title.")
        if not link_url:
            missing.append("The product has no page address (slug).")
        if not img_list:
            missing.append("Add at least one product image.")
        if p is None:
            missing.append("Set a selling price above 0.")
        if missing:
            problems.extend(missing)
            return None
        sell = _with_tax(p, product)
        list_price = _money(mrp)
        list_price = _with_tax(list_price, product) if list_price else None
        a = dict(common, **attrs)
        a["title"] = title[:TITLE_MAX]
        a["link"] = link_url
        if img_list:
            a["imageLink"] = img_list[0]
            if len(img_list) > 1:
                a["additionalImageLinks"] = img_list[1:1 + ADDITIONAL_IMAGES_MAX]
        if list_price and list_price > sell:
            a["price"] = _price_obj(list_price, currency)
            a["salePrice"] = _price_obj(sell, currency)
        else:
            a["price"] = _price_obj(sell, currency)
        a["availability"] = "IN_STOCK" if stock is None or stock > 0 else "OUT_OF_STOCK"
        for va in variant_attrs or []:
            name, value = _norm_name(va.get("name")), str(va.get("value") or "").strip()
            if not value:
                continue
            if name in _COLOR_NAMES:
                a["color"] = value[:100]
            elif name in _SIZE_NAMES:
                a["size"] = value[:100]
            elif name in _MATERIAL_NAMES:
                a["material"] = value[:200]
            elif name in _PATTERN_NAMES:
                a["pattern"] = value[:100]
            elif name in _GENDER_NAMES and _gender(value):
                a["gender"] = _gender(value)
            elif name in _AGE_NAMES and _age_group(value):
                a["ageGroup"] = _age_group(value)
        return {
            "offerId": offer_id,
            "contentLanguage": config.content_language,
            "feedLabel": config.effective_feed_label,
            "productAttributes": a,
        }

    combos = [c for c in combos_of(product) if attrs_of(c)]
    offers = []
    if not combos:
        stock = product.inventory if product.inventory is not None else None
        o = finish(
            offer_id_for(product), {},
            price=product.price, mrp=product.mrp, stock=stock, img_list=images, link_url=link,
        )
        if o:
            offers.append(o)
    else:
        group = offer_id_for(product)
        for combo in combos:
            vattrs = [a for a in attrs_of(combo) if a["value"]]
            label = " / ".join(a["value"] for a in vattrs)
            c_imgs = [u for u in (_abs_url(base, x) for x in combo.get("images") or []) if u]
            img_list = list(dict.fromkeys(c_imgs + images))
            price = combo.get("price") if combo.get("price") not in (None, "") else product.price
            mrp = combo.get("mrp") if combo.get("mrp") not in (None, "") else product.mrp
            try:
                stock = int(combo.get("inventory") or 0)
            except (TypeError, ValueError):
                stock = 0
            v_link = f"{link}?{urlencode({'variant': str(combo.get('key') or '')})}" if link else ""
            o = finish(
                offer_id_for(product, combo),
                {"itemGroupId": group},
                price=price, mrp=mrp, stock=stock, img_list=img_list, link_url=v_link,
                variant_attrs=vattrs,
            )
            if o:
                # Title carries the variation so shoppers see what they click.
                o["productAttributes"]["title"] = (f"{title} - {label}" if label else title)[:TITLE_MAX]
                if combo.get("sku"):
                    o["customAttributes"] = [{"name": "sku", "value": str(combo["sku"])[:100]}]
                offers.append(o)
    if product.sku and offers and not combos:
        offers[0]["customAttributes"] = [{"name": "sku", "value": product.sku[:100]}]
    return offers, list(dict.fromkeys(problems))


def _payload_hash(offers: list, config) -> str:
    blob = json.dumps(
        {"o": offers, "ds": config.data_source, "m": config.merchant_id}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------
def config_for(site):
    from commerce.models import GoogleMerchantConfig

    return GoogleMerchantConfig.objects.for_site(site).select_related("site").first()


def _status_row(product):
    from commerce.models import GoogleMerchantProductStatus

    row = GoogleMerchantProductStatus.objects.for_site(product.site).filter(product=product).first()
    return row or GoogleMerchantProductStatus(site=product.site, product=product)


def sync_product(product, config, *, client=None, force=False) -> str:
    """Bring Google in line with one product. Returns the resulting state.

    Inactive / deleted products and vanished variations are removed; listable
    offers are upserted (skipped when nothing changed since the last push,
    unless ``force``). Raises :class:`MerchantError` only for fatal errors.
    """
    from commerce.models import GoogleMerchantProductStatus as S

    client = client or MerchantClient(config)
    row = _status_row(product)
    previous = list(row.offer_ids or [])
    listable = product.is_active and not product.is_deleted

    offers, problems = (build_offers(product, config) if listable else ([], []))
    keep = [o["offerId"] for o in offers]
    now = timezone.now()
    try:
        for oid in [x for x in previous if x not in keep]:
            client.delete_offer(oid)
        if not listable:
            row.state, row.issues, row.last_error = S.State.REMOVED, [], ""
            row.offer_ids, row.payload_hash = [], ""
        elif not offers:
            row.state, row.offer_ids, row.payload_hash = S.State.INVALID, [], ""
            row.issues = [{"severity": "ERROR", "description": p} for p in problems]
            row.last_error = " ".join(problems)
        else:
            digest = _payload_hash(offers, config)
            if force or digest != row.payload_hash or set(previous) != set(keep):
                for payload in offers:
                    client.insert_offer(payload)
                row.payload_hash = digest
                if row.state in (S.State.ERROR, S.State.INVALID, S.State.REMOVED, ""):
                    row.state = S.State.PENDING
            row.offer_ids = keep
            row.last_error = ""
            if problems:  # some variations skipped
                row.issues = [{"severity": "WARNING", "description": p} for p in problems]
        row.last_synced_at = now
    except MerchantError as exc:
        row.state, row.last_error = S.State.ERROR, exc.message
        row.last_synced_at = now
        if row.pk or listable:
            row.save()
        if exc.fatal:
            raise
        return row.state
    if row.pk or listable or previous:
        row.save()
    return row.state


def remove_offers(config, offer_ids) -> None:
    """Delete specific offers (used when a product row is hard-deleted)."""
    if not offer_ids or not config or not config.is_connected:
        return
    client = MerchantClient(config)
    client.ensure_data_source()
    for oid in offer_ids:
        try:
            client.delete_offer(oid)
        except MerchantError as exc:
            if exc.fatal:
                return


def full_sync(config, *, force=False, purge=None) -> dict:
    """Sync the whole catalogue, then read back statuses. Updates live progress
    on the config so the dashboard can show it.

    ``purge`` (``{"language", "feed_label", "data_source"}``) first deletes every
    known offer under a previous target, after the owner changed the feed's
    country or language."""
    from commerce.models import GoogleMerchantProductStatus, Product

    site = config.site
    model = type(config)
    from core.queries import sorted_rows

    # Every product row joins the store's whole ``content`` JSON; sorting those
    # (default newest-first order) can exceed MySQL's sort buffer (error 1038).
    products = sorted_rows(
        Product.objects.for_site(site)
        .filter(is_deleted=False)
        .select_related("category", "category__parent", "site")
    )
    total = len(products)
    progress = {"total": total, "done": 0, "failed": 0, "invalid": 0}
    model.all_objects.filter(pk=config.pk).update(
        sync_state=model.SyncState.RUNNING, sync_started_at=timezone.now(),
        sync_progress=progress, last_error="",
    )
    client = MerchantClient(config)
    error = ""
    try:
        if purge and purge.get("data_source"):
            for row in GoogleMerchantProductStatus.objects.for_site(site):
                for oid in row.offer_ids or []:
                    try:
                        client.delete_offer(
                            oid, language=purge.get("language"),
                            feed_label=purge.get("feed_label"), data_source=purge.get("data_source"),
                        )
                    except MerchantError as exc:
                        if exc.fatal:
                            raise
            GoogleMerchantProductStatus.objects.for_site(site).update(offer_ids=[], payload_hash="")
        client.ensure_data_source()
        for i, product in enumerate(products, 1):
            state = sync_product(product, config, client=client, force=force)
            if state == GoogleMerchantProductStatus.State.ERROR:
                progress["failed"] += 1
            elif state == GoogleMerchantProductStatus.State.INVALID:
                progress["invalid"] += 1
            progress["done"] = i
            if i % 5 == 0 or i == total:
                model.all_objects.filter(pk=config.pk).update(sync_progress=dict(progress))
        # Products deleted outright while disconnected: take their offers down.
        live_ids = {p.pk for p in products}
        for row in GoogleMerchantProductStatus.objects.for_site(site).exclude(product_id__in=live_ids):
            for oid in row.offer_ids or []:
                try:
                    client.delete_offer(oid)
                except MerchantError:
                    pass
        try:
            refresh_statuses(config, client=client)
        except MerchantError as exc:
            if exc.fatal:
                raise
    except MerchantError as exc:
        error = exc.message
        if exc.fatal and (exc.status == 401 or exc.reason == "invalid_grant"):
            model.all_objects.filter(pk=config.pk).update(status=model.Status.ERROR)
    finally:
        model.all_objects.filter(pk=config.pk).update(
            sync_state=model.SyncState.IDLE,
            sync_progress=dict(progress),
            last_synced_at=timezone.now(),
            last_error=error,
        )
    return progress


def _state_from_status(status: dict) -> tuple[str, list]:
    """Map Google's ``productStatus`` to our state and a trimmed issue list."""
    from commerce.models import GoogleMerchantProductStatus as S

    dests = status.get("destinationStatuses") or []
    issues = []
    worst = ""
    for i in status.get("itemLevelIssues") or []:
        sev = str(i.get("severity") or "").upper()
        issues.append({
            "severity": sev,
            "code": i.get("code") or "",
            "description": i.get("description") or i.get("detail") or i.get("code") or "",
            "detail": i.get("detail") or "",
            "attribute": i.get("attribute") or "",
            "documentation": i.get("documentation") or "",
        })
        if sev == "DISAPPROVED":
            worst = "disapproved"
        elif sev == "DEMOTED" and worst != "disapproved":
            worst = "demoted"
    approved = any(d.get("approvedCountries") for d in dests)
    pending = any(d.get("pendingCountries") for d in dests)
    disapproved = any(d.get("disapprovedCountries") for d in dests)
    if approved:
        state = S.State.WARNING if issues else S.State.APPROVED
    elif disapproved or worst == "disapproved":
        state = S.State.DISAPPROVED
    elif pending or not dests:
        state = S.State.PENDING
    else:
        state = S.State.PENDING
    return state, issues[:20]


def refresh_statuses(config, *, client=None) -> dict:
    """Read processed products back from Google and update every product's
    state and issues, plus the approval tally shown on the dashboard."""
    from commerce.models import GoogleMerchantProductStatus as S

    client = client or MerchantClient(config)
    by_offer = {}
    for p in client.list_products():
        oid = str(p.get("offerId") or "")
        if oid:
            by_offer[oid] = p.get("productStatus") or {}
    rank = {S.State.DISAPPROVED: 4, S.State.WARNING: 3, S.State.PENDING: 2, S.State.APPROVED: 1}
    summary = {"approved": 0, "warning": 0, "pending": 0, "disapproved": 0, "invalid": 0, "error": 0}
    rows = S.objects.for_site(config.site).exclude(state=S.State.REMOVED)
    for row in rows:
        if row.state in (S.State.INVALID, S.State.ERROR) and not row.offer_ids:
            summary[row.state] += 1
            continue
        states, issues = [], []
        for oid in row.offer_ids or []:
            if oid in by_offer:
                st, iss = _state_from_status(by_offer[oid])
                states.append(st)
                issues += iss
        if states:
            row.state = max(states, key=lambda s: rank.get(s, 0))
            # Deduplicate the same issue reported for several variations.
            seen, uniq = set(), []
            for i in issues:
                k = (i["code"], i["attribute"], i["description"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(i)
            row.issues = uniq[:20]
            row.save(update_fields=["state", "issues", "updated_at"])
        summary[row.state] = summary.get(row.state, 0) + 1
    model = type(config)
    now = timezone.now()
    model.all_objects.filter(pk=config.pk).update(status_summary=summary, statuses_refreshed_at=now)
    config.status_summary, config.statuses_refreshed_at = summary, now
    return summary


# --------------------------------------------------------------------------
# Queueing (signals / views)
# --------------------------------------------------------------------------
def queue_product_sync(product) -> None:
    """Queue a single-product sync after the current transaction commits, when
    the store is connected with auto-sync on. Cheap no-op otherwise."""
    try:
        site_id = product.site_id
        from commerce.models import GoogleMerchantConfig

        cfg = (
            GoogleMerchantConfig.all_objects.filter(site_id=site_id)
            .only("status", "auto_sync", "merchant_id").first()
        )
        if not cfg or cfg.status != cfg.Status.CONNECTED or not cfg.auto_sync:
            return
        pid = str(product.pk)

        def _send():
            from commerce.tasks import google_merchant_sync_product

            try:
                google_merchant_sync_product.apply_async(args=[pid], countdown=15)
            except Exception:
                logger.exception("queueing merchant sync failed product=%s", pid)

        transaction.on_commit(_send)
    except Exception:
        logger.exception("merchant auto-sync hook failed")


def queue_offer_removal(product) -> None:
    """A product is being hard-deleted: capture its offers now (the status row
    is deleted with it) and remove them from Google after commit."""
    try:
        from commerce.models import GoogleMerchantProductStatus

        row = GoogleMerchantProductStatus.all_objects.filter(product_id=product.pk).first()
        offers = list(row.offer_ids or []) if row else []
        if not offers:
            return
        site_id = str(product.site_id)

        def _send():
            from commerce.tasks import google_merchant_remove_offers

            try:
                google_merchant_remove_offers.delay(site_id, offers)
            except Exception:
                logger.exception("queueing merchant offer removal failed")

        transaction.on_commit(_send)
    except Exception:
        logger.exception("merchant delete hook failed")


# --------------------------------------------------------------------------
# Platform: one-time developer registration
# --------------------------------------------------------------------------
def try_platform_registration(config) -> bool:
    """Register OneWooz's Cloud project using ``config``'s Google sign-in, if
    that sign-in is an ADMIN of OneWooz's own Merchant Center account
    (``settings.GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID``).

    Lets the one-time platform setup happen by simply connecting OneWooz's own
    account from any store, with no admin action. Only ever registers against
    the platform account, never a merchant's (Google's guidance). Returns True
    when the project is (now or already) registered; any other outcome, e.g. a
    store owner who isn't an admin there, is a quiet False.
    """
    platform_id = re.sub(r"\D", "", str(getattr(settings, "GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", "") or ""))
    if not platform_id or not getattr(config, "refresh_token", ""):
        return False
    body = {}
    email = getattr(settings, "GOOGLE_MERCHANT_DEVELOPER_EMAIL", "") or config.google_email
    if email:
        body["developerEmail"] = email
    try:
        MerchantClient(config).request(
            "POST",
            f"{API}/accounts/v1/accounts/{platform_id}/developerRegistration:registerGcp",
            body=body,
            attempts=2,  # one forced token refresh on a 401, no 5xx retries
        )
    except MerchantError as exc:
        if "ALREADY" in f"{exc.reason} {exc.message}".upper():
            return True
        logger.info("platform registration via %s not possible: %s", config.google_email, exc.reason or exc.message)
        return False
    logger.warning("merchant: Cloud project registered with platform account %s via %s", platform_id, config.google_email)
    return True


def register_platform_project(config, developer_email: str = "", account_id: str = "") -> dict:
    """Register OneWooz's Cloud project with a Merchant Center account
    (``account_id``, default ``config.merchant_id``), which must be OneWooz's
    own primary account; ``config``'s Google sign-in must be an ADMIN there.
    Done once for the platform."""
    body = {}
    email = developer_email or getattr(settings, "GOOGLE_MERCHANT_DEVELOPER_EMAIL", "") or config.google_email
    if email:
        body["developerEmail"] = email
    target = re.sub(r"\D", "", str(account_id or config.merchant_id or ""))
    if not target:
        raise MerchantError("Enter OneWooz's own Merchant Center ID.")
    return MerchantClient(config).request(
        "POST",
        f"{API}/accounts/v1/accounts/{target}/developerRegistration:registerGcp",
        body=body,
        attempts=2,  # one forced token refresh on a 401, no 5xx retries
    )
