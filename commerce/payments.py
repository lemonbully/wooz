"""Payment gateways: Razorpay and Cashfree.

The trust model: **we never mark an order paid on the strength of a redirect or
webhook body.** A redirect is confirmed by verifying the gateway's signature
with the store's own secret (Razorpay) or by fetching the order status from the
gateway's authenticated API (Cashfree); a webhook is only a nudge to go and
fetch the authoritative status. So a forged callback can't confirm a payment.

Credentials live per-store in :class:`~commerce.models.PaymentGatewayConfig`
(encrypted). Gateway SDK / HTTP imports are lazy so a store without a gateway
never pays the import cost and a missing package can't break module load.
"""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from commerce.models import Order, Payment, PaymentGatewayConfig

logger = logging.getLogger("onewooz.audit")

_CASHFREE_BASES = {
    "production": "https://api.cashfree.com/pg",
    "sandbox": "https://sandbox.cashfree.com/pg",
}
_CASHFREE_API_VERSION = "2023-08-01"
# Remember which environment a store's keys authenticated against, so later
# calls go straight there instead of probing again.
_CASHFREE_ENV_TTL = 60 * 60 * 24 * 30


class PaymentError(Exception):
    """A gateway call failed. The message is safe to log, not to show raw.

    ``gateway_message`` carries the provider's own explanation (e.g. Cashfree's
    "authentication Failed") for the store owner, never for the shopper.
    """

    def __init__(self, message: str, *, gateway_message: str = ""):
        super().__init__(message)
        self.gateway_message = gateway_message


def gateway_config(site):
    return PaymentGatewayConfig.objects.for_site(site).first()


# --------------------------------------------------------------------------
# Cashfree environment + transport
# --------------------------------------------------------------------------
# Cashfree keys are environment-bound: TEST keys only authenticate against
# sandbox.cashfree.com, PROD keys only against api.cashfree.com. The environment
# used to come from a global setting (DEBUG), so a store whose keys didn't match
# the server's mode got a 401 on every checkout. The environment is a property
# of the store's keys, so derive it from them.
def _cashfree_env_cache_key(config) -> str:
    digest = hashlib.sha256((config.key_id or "").strip().encode()).hexdigest()[:24]
    return f"cashfree-env:{digest}"


def _cashfree_env_guess(config) -> str:
    """Best guess at the environment ``config``'s keys belong to."""
    secret = (config.key_secret or "").strip().lower()
    app_id = (config.key_id or "").strip().lower()
    # Current Cashfree secrets are self-describing: cfsk_ma_prod_… / cfsk_ma_test_…
    if "_prod_" in secret:
        return "production"
    if "_test_" in secret or app_id.startswith("test"):
        return "sandbox"
    remembered = cache.get(_cashfree_env_cache_key(config))
    if remembered in _CASHFREE_BASES:
        return remembered
    sandbox = getattr(settings, "CASHFREE_SANDBOX", None)
    if sandbox is None:
        sandbox = settings.DEBUG
    return "sandbox" if sandbox else "production"


def _cashfree_headers(config) -> dict:
    return {
        "x-client-id": (config.key_id or "").strip(),
        "x-client-secret": (config.key_secret or "").strip(),
        "x-api-version": _CASHFREE_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _cashfree_call(config, method: str, path: str, *, body: dict | None = None, timeout: int = 20):
    """Call Cashfree in the keys' environment; returns ``(env, status, data)``.

    Tries the guessed environment first. A 401 there means the keys belong to
    the other environment, so that one is tried next (safe for order creation:
    nothing is created on an authentication failure). The environment that
    answers is remembered. Raises ``requests`` exceptions on transport failure.
    """
    import requests

    first = _cashfree_env_guess(config)
    second = "sandbox" if first == "production" else "production"
    result = None
    for env in (first, second):
        resp = requests.request(
            method,
            f"{_CASHFREE_BASES[env]}{path}",
            json=body,
            headers=_cashfree_headers(config),
            timeout=timeout,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"message": (resp.text or "")[:300]}
        if not isinstance(data, dict):
            data = {"message": str(data)[:300]}
        if resp.status_code != 401:
            cache.set(_cashfree_env_cache_key(config), env, _CASHFREE_ENV_TTL)
            return env, resp.status_code, data
        if result is None:
            result = (env, resp.status_code, data)
    # Both environments refused the keys; report the first (expected) one.
    return result


def _cashfree_message(data: dict) -> str:
    message = str(data.get("message") or "").strip()
    code = str(data.get("code") or data.get("type") or "").strip()
    if code and message:
        return f"{message} ({code})"
    return message or code or "unknown error"


def check_cashfree_credentials(config) -> tuple[bool, str, str]:
    """Probe a store's Cashfree keys without creating anything.

    Fetching an order id that cannot exist answers 404 with valid keys and 401
    with invalid ones. Returns ``(ok, env, message)``.
    """
    try:
        env, status, data = _cashfree_call(
            config, "GET", "/orders/onewooz-credential-check", timeout=10
        )
    except Exception as exc:
        return False, "", f"Couldn’t reach Cashfree ({exc.__class__.__name__})."
    if status == 401:
        return False, env, _cashfree_message(data)
    return True, env, ""


def _cashfree_phone(raw: str) -> str:
    """A 10-digit Indian mobile, as Cashfree expects (it rejects spaces, dashes
    and prefixes). Falls back to a placeholder the shopper can correct inside
    Cashfree's own checkout."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else "9999999999"


def _cashfree_email(raw: str) -> str:
    """The shopper's email if Cashfree will accept it, else "" (it's optional)."""
    email = (raw or "").strip()
    if not email or len(email) > 100:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        return ""
    return email


def _to_paise(amount) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


# --------------------------------------------------------------------------
# Create a gateway order (called at checkout)
# --------------------------------------------------------------------------
def create_gateway_order(site, order: Order, config: PaymentGatewayConfig, *, return_url: str, notify_url: str, amount=None) -> dict:
    """Create the gateway-side order + a ``processing`` Payment, and return the
    parameters the storefront needs to open the gateway's checkout.

    ``amount`` charges a partial amount (the online portion of a partial-payment
    order); it defaults to the full order total. The created ``Payment`` carries
    the charged amount, and both provider builders read it  so the gateway is
    always asked to charge exactly what the Payment records.
    """
    charge = order.total if amount is None else Decimal(amount)
    payment = Payment.objects.create(
        site=site,
        order=order,
        gateway=config.gateway,
        amount=charge,
        currency="INR",
        status=Payment.Status.PROCESSING,
    )

    if config.gateway == PaymentGatewayConfig.Gateway.RAZORPAY:
        return _razorpay_create(site, order, config, payment)
    if config.gateway == PaymentGatewayConfig.Gateway.CASHFREE:
        return _cashfree_create(site, order, config, payment, return_url=return_url, notify_url=notify_url)
    raise PaymentError(f"Unsupported gateway: {config.gateway!r}")


def _razorpay_create(site, order, config, payment) -> dict:
    try:
        import razorpay
    except ImportError as exc:  # pragma: no cover
        raise PaymentError("razorpay package not installed") from exc

    client = razorpay.Client(auth=(config.key_id, config.key_secret))
    try:
        rp = client.order.create(
            {
                "amount": _to_paise(payment.amount),
                "currency": "INR",
                # The order's real number is assigned only once it's paid.
                "receipt": f"pay-{payment.id.hex[:32]}",
                "notes": {"order_id": str(order.id), "payment_id": str(payment.id)},
            }
        )
    except Exception as exc:
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status"])
        raise PaymentError(f"razorpay order.create failed: {exc}") from exc

    payment.provider_order_id = rp["id"]
    payment.save(update_fields=["provider_order_id"])
    return {
        "gateway": "razorpay",
        "key_id": config.key_id,
        "order_id": rp["id"],
        "amount": _to_paise(payment.amount),
        "currency": "INR",
        "name": site.name,
        "payment_id": str(payment.id),
        "prefill": {
            "name": order.customer_name,
            "email": order.customer_email,
            "contact": order.customer_phone,
        },
    }


def _cashfree_create(site, order, config, payment, *, return_url, notify_url) -> dict:
    def fail(reason: str, gateway_message: str = "", exc: Exception | None = None):
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=["status"])
        logger.error(
            "cashfree create failed: site=%s order=%s amount=%s reason=%s",
            site.pk, order.number, payment.amount, reason,
        )
        raise PaymentError(f"cashfree create failed: {reason}", gateway_message=gateway_message or reason) from exc

    amount = Decimal(payment.amount).quantize(Decimal("0.01"))
    if amount < Decimal("1.00"):
        fail("order amount is below Cashfree's ₹1 minimum")

    # Cashfree: order_id 3–45 chars of [A-Za-z0-9_-]. Keyed on the payment
    # attempt (the order only gets its real number once paid), so it is unique.
    cf_order_id = f"OW-{payment.id.hex}"
    customer = {
        "customer_id": (
            str(order.customer_id) if order.customer_id else f"guest_{payment.id.hex[-12:]}"
        ),
        "customer_phone": _cashfree_phone(order.customer_phone),
    }
    name = " ".join((order.customer_name or "").split())
    if len(name) >= 3:
        customer["customer_name"] = name[:100]
    email = _cashfree_email(order.customer_email)
    if email:
        customer["customer_email"] = email

    # Cashfree only accepts https callback URLs (production refuses http ones
    # outright), so a plain-http dev URL is left out rather than failing the
    # order; the in-page modal and verify-by-fetch confirm the payment anyway.
    meta = {}
    if (return_url or "").startswith("https://"):
        meta["return_url"] = return_url
    if (notify_url or "").startswith("https://"):
        meta["notify_url"] = notify_url

    body = {
        "order_id": cf_order_id,
        "order_amount": float(amount),
        "order_currency": "INR",
        "customer_details": customer,
        "order_note": f"{site.name} online order"[:200],
    }
    if meta:
        body["order_meta"] = meta

    try:
        env, status, data = _cashfree_call(config, "POST", "/orders", body=body)
    except Exception as exc:
        fail(f"couldn’t reach Cashfree ({exc.__class__.__name__}: {exc})",
             gateway_message="Couldn’t reach Cashfree.", exc=exc)

    if status >= 300 or not data.get("payment_session_id"):
        message = _cashfree_message(data)
        if status == 401:
            message = (
                f"{message}. Check the App ID and Secret Key in Payment settings "
                "(test keys and live keys can’t be mixed)."
            )
        fail(f"HTTP {status} env={env}: {message}", gateway_message=message)

    payment.provider_order_id = cf_order_id
    payment.save(update_fields=["provider_order_id"])
    return {
        "gateway": "cashfree",
        "payment_session_id": data["payment_session_id"],
        "order_id": cf_order_id,
        "payment_id": str(payment.id),
        # The browser SDK must open checkout in the environment the session
        # was created in, or it rejects the session id.
        "mode": env,
    }


# --------------------------------------------------------------------------
# Confirm a payment (redirect return + webhook both funnel through here)
# --------------------------------------------------------------------------
def _mark_paid(payment: Payment, *, provider_payment_id: str = "", signature: str = "") -> None:
    """Idempotently mark a payment + its order paid.

    The browser verify, the return URL and the webhook can all confirm the same
    payment at once, so each step is claimed with a conditional UPDATE: only
    the caller that actually flips the row carries on, and the order is placed
    exactly once.
    """
    if payment.status == Payment.Status.PAID:
        return
    changes = {"status": Payment.Status.PAID}
    if provider_payment_id:
        changes["provider_payment_id"] = provider_payment_id
    if signature:
        changes["provider_signature"] = signature[:256]
    claimed = (
        Payment.all_objects.filter(pk=payment.pk)
        .exclude(status=Payment.Status.PAID)
        .update(**changes)
    )
    if not claimed:
        return  # another confirmation got here first
    for field, value in changes.items():
        setattr(payment, field, value)

    order = Order.all_objects.select_related("site").get(pk=payment.order_id)
    payment.order = order
    if order.status == Order.Status.AWAITING_PAYMENT:
        # The online payment is in: this is the moment the order is placed.
        # Partial: the balance is collected as COD, so it waits in Pending for
        # the owner to accept (never jumps straight to CONFIRMED). Full: PAID.
        if order.is_partial_payment:
            status, method = Order.Status.CREATED, "partial"
        else:
            status, method = Order.Status.PAID, payment.gateway
        placed = Order.all_objects.filter(
            pk=order.pk, status=Order.Status.AWAITING_PAYMENT
        ).update(status=status, payment_method=method)
        if placed:
            order.status = status
            order.payment_method = method
            order.assign_placed_number()
            # A real save as well, so the status hooks (collaborator credit,
            # platform charge, timestamps) run for the new status.
            order.save(update_fields=["status", "payment_method", "confirmed_at", "updated_at"])
            from commerce.checkout import place_order

            place_order(order)
    elif order.status in (Order.Status.CREATED, Order.Status.CONFIRMED):
        # Orders created before online orders were held back until payment.
        if order.is_partial_payment:
            # The online portion is captured; the balance is collected as COD on
            # delivery. The order still waits in Pending until the owner accepts
            # it (or auto-ship books it)  it is never jumped straight to
            # CONFIRMED, so it can't skip acceptance.
            order.payment_method = "partial"
        else:
            order.status = Order.Status.PAID
            order.payment_method = payment.gateway
        order.save(update_fields=["status", "payment_method", "confirmed_at", "updated_at"])

    # Auto-create shipping when the store has the toggle on: book the courier now
    # (a real Shiprocket order) and promote the order to CONFIRMED so it leaves
    # Pending for the courier tab. With the toggle off this is a no-op and the
    # order stays in Pending until the owner accepts it. Best-effort  a courier
    # hiccup must never fail an already-confirmed payment.
    from commerce import shipping as shipping_svc

    if order.status in (Order.Status.CREATED, Order.Status.PAID, Order.Status.CONFIRMED):
        try:
            shipping_svc.auto_ship_if_enabled(order.site, order)
        except Exception:
            logger.warning("post-payment auto-ship failed order=%s", order.number, exc_info=True)

    logger.warning(
        "payment confirmed: order=%s gateway=%s partial=%s",
        order.number, payment.gateway, order.is_partial_payment,
    )


def verify_razorpay_return(payment: Payment, config, *, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """Verify a Razorpay redirect using the store's key_secret (HMAC of
    ``order_id|payment_id``). No API round-trip needed  this is authoritative."""
    try:
        import razorpay
    except ImportError:
        return False
    client = razorpay.Client(auth=(config.key_id, config.key_secret))
    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": payment.provider_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    except Exception:
        return False
    _mark_paid(payment, provider_payment_id=razorpay_payment_id, signature=razorpay_signature)
    return True


def confirm_razorpay_by_fetch(payment: Payment, config) -> bool:
    """Fetch the order's payments from Razorpay and confirm if any is captured.
    Used by the webhook (never trusts the webhook body)."""
    try:
        import razorpay
    except ImportError:
        return False
    client = razorpay.Client(auth=(config.key_id, config.key_secret))
    try:
        payments = client.order.payments(payment.provider_order_id)
    except Exception as exc:
        logger.warning("razorpay fetch failed: %s", exc)
        return False
    for p in (payments or {}).get("items", []):
        if p.get("status") == "captured":
            _mark_paid(payment, provider_payment_id=p.get("id", ""))
            return True
    return False


def confirm_cashfree_by_fetch(payment: Payment, config) -> bool:
    """Fetch the order status from Cashfree and confirm if PAID. Used by both the
    redirect return and the webhook."""
    if not payment.provider_order_id or config is None:
        return False
    try:
        _env, status, data = _cashfree_call(config, "GET", f"/orders/{payment.provider_order_id}")
    except Exception as exc:
        logger.warning("cashfree fetch failed: %s", exc)
        return False
    if status >= 300:
        logger.warning(
            "cashfree fetch rejected: order=%s status=%s %s",
            payment.provider_order_id, status, _cashfree_message(data),
        )
        return False
    if str(data.get("order_status")).upper() == "PAID":
        _mark_paid(payment, provider_payment_id=str(data.get("cf_order_id") or ""))
        return True
    return False


def confirm_by_fetch(payment: Payment, config) -> bool:
    """Gateway-agnostic authoritative confirmation."""
    if payment.gateway == PaymentGatewayConfig.Gateway.RAZORPAY:
        return confirm_razorpay_by_fetch(payment, config)
    if payment.gateway == PaymentGatewayConfig.Gateway.CASHFREE:
        return confirm_cashfree_by_fetch(payment, config)
    return False


def payment_for_provider_order(provider_order_id: str):
    """Find a Payment (across stores) by its gateway order id  used by webhooks,
    which arrive without a host that identifies the tenant."""
    if not provider_order_id:
        return None
    from core.queries import first_row

    # ``.first()`` sorts the match(es) by created_at, and each row carries the
    # joined store's whole ``content`` JSON; sorting keys only keeps payment
    # webhooks clear of MySQL's sort-memory limit (error 1038).
    return first_row(
        Payment.objects.across_all_sites()
        .filter(provider_order_id=provider_order_id)
        .select_related("order", "site")
    )
