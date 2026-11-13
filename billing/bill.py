"""The owner's subscription bill, as shown on the profile page.

A faithful port of the 1matrix dashboard bill (Help & Support → Billing →
Plan Details / INVOICE): the same sections, the same fields and, deliberately,
the same arithmetic:

* the bill is raised on the **latest paid** subscription order;
* ``taxable = paid ÷ 1.18`` (the paid amount is GST-inclusive at 18%);
* ``rate = taxable ÷ months`` (shown "per month");
* a buyer in the seller's own state pays **CGST 9% + SGST 9%** of the taxable
  amount, anyone else **IGST 18%**;
* "Discount Applied" is the order's discount as a percentage of its base price.

Money is ``Decimal`` rounded half-up to paise (as the reference's
``floatformat:2`` rendered it). The seller block (company, mobile, email,
address, GSTIN, HSN, state, Terms & Conditions, signature) comes from the active
"Invoice details" object in the admin (``billing.InvoiceDetails``), any blank
field falling back to ``settings.PLATFORM_BILLING_ENTITY``. The buyer block
comes from the account's onboarding (tax) profile, falling back to the account
and the order itself. States are resolved with ``billing.gst`` (a GSTIN's state
code wins over a typed state).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

_TWO = Decimal("0.01")
_GST_DIVISOR = Decimal("1.18")
_FULL_GST = Decimal("0.18")
_HALF_GST = Decimal("0.09")


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(_TWO, rounding=ROUND_HALF_UP)


def _current_details():
    """The active admin "Invoice details" object, or None."""
    from billing.models import InvoiceDetails

    try:
        return InvoiceDetails.current()
    except Exception:  # table not migrated yet: keep the bill rendering
        return None


def seller() -> dict:
    """The seller block printed on the bill.

    Each field is the active Invoice details object's value when it is filled,
    else the settings fallback. ``state`` is the GSTIN's state when the GSTIN is
    valid (the registered place of supply), else the named state.
    """
    from django.templatetags.static import static

    from billing import gst

    base = dict(getattr(settings, "PLATFORM_BILLING_ENTITY", {}) or {})
    obj = _current_details()

    def pick(attr, key):
        val = (getattr(obj, attr, "") or "") if obj is not None else ""
        val = val.strip() if isinstance(val, str) else val
        return val or base.get(key) or ""

    # GSTIN + state are one tax identity: if the admin object sets either, use
    # the object's pair as-is (never a fallback GSTIN that would override the
    # object's state); only with neither set do both come from the fallback.
    obj_gstin = gst.normalise_gstin(getattr(obj, "gstin", "") if obj is not None else "")
    obj_state = ((getattr(obj, "state", "") or "").strip()) if obj is not None else ""
    if obj_gstin or obj_state:
        gstin, raw_state = obj_gstin, obj_state
    else:
        gstin, raw_state = gst.normalise_gstin(base.get("gstin") or ""), base.get("state") or ""
    terms = list(getattr(obj, "terms", None) or []) if obj is not None else []
    if not terms:
        terms = list(base.get("terms") or [])

    sig_url = obj.signature.url if (obj is not None and obj.signature) else static("img/billing/signature.png")
    return {
        "company_name": pick("company_name", "company_name"),
        "mobile": pick("mobile_number", "mobile"),
        "email": pick("email", "email"),
        "address": pick("address", "address"),
        "gstin": gstin,
        "hsn": pick("hsn_code", "hsn") or "998313",
        "state": gst.effective_state(raw_state, gstin),
        "terms": [str(t).strip() for t in terms if str(t).strip()],
        "signature_url": sig_url,
        "signatory_designation": pick("designation", "signatory_designation"),
    }


_SIGNATURE_MAX_BYTES = 2 * 1024 * 1024
_STATIC_SIGNATURE = "img/billing/signature.png"


def _data_uri(raw: bytes, name: str) -> str:
    import base64
    import mimetypes

    mime = mimetypes.guess_type(name or "")[0] or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _signature_data_uri() -> str:
    """The bill's signature image inlined as a ``data:`` URI, or "".

    The uploaded signature (and the bundled fallback) is served from the media
    / static CDN, a different origin than the dashboard. html2canvas silently
    drops a cross-origin image that lacks CORS headers, so the downloaded PDF
    came out without the signature. Inlined, it is same-document and always
    captured. Any read failure returns "" and the caller keeps the plain URL.
    """
    obj = _current_details()
    try:
        if obj is not None and obj.signature:
            with obj.signature.open("rb") as fh:
                raw = fh.read(_SIGNATURE_MAX_BYTES + 1)
            if raw and len(raw) <= _SIGNATURE_MAX_BYTES:
                return _data_uri(raw, obj.signature.name)
            return ""
    except Exception:
        return ""

    try:
        from django.contrib.staticfiles import finders

        path = finders.find(_STATIC_SIGNATURE)
        if path:
            with open(path, "rb") as fh:
                return _data_uri(fh.read(), _STATIC_SIGNATURE)
        from django.contrib.staticfiles.storage import staticfiles_storage

        with staticfiles_storage.open(_STATIC_SIGNATURE, "rb") as fh:
            return _data_uri(fh.read(), _STATIC_SIGNATURE)
    except Exception:
        return ""


def latest_paid_order(user):
    """The account's most recent settled (PAID) subscription order, or None.

    Any amount counts, ₹0 included: a free-plan grant, a 100%-discount code and
    a ₹0 admin invite all settle as PAID ₹0 orders, and each is a real
    subscription the owner should be able to see a bill for. (Requiring a
    non-zero total is what hid the button for those owners.)
    """
    from django.db.models import F

    from billing.models import SubscriptionOrder

    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return None
    return (
        SubscriptionOrder.objects.filter(
            customer_email__iexact=email,
            status=SubscriptionOrder.Status.PAID,
        )
        .order_by(F("paid_at").desc(nulls_last=True), "-created_at")
        .first()
    )


def _profile(user):
    try:
        return user.onboarding_profile
    except Exception:  # RelatedObjectDoesNotExist, or no relation at all
        return None


def build_bill(user, order=None) -> dict | None:
    """Everything the bill template renders, or None when the account has no
    settled subscription order at all.

    Pass ``order`` (the order behind the subscription the profile card shows,
    ``CurrentSubscription.order``) so the bill is always for the same
    subscription the owner is looking at; without it, the latest settled order.
    """
    if order is None or getattr(order, "status", None) != "paid":
        order = latest_paid_order(user)
    if order is None:
        return None
    return _bill_for(user, order)


def build_sample_bill(user) -> dict:
    """A realistic bill for previewing the layout on localhost (DEBUG only).

    Uses the account's latest paid order when there is one; otherwise an
    unsaved sample order (Yearly, ₹11,800 incl. 18% GST, 10% discount) so the
    whole bill, including its tax split and download, can be checked without
    making a purchase. Nothing is written to the database.
    """
    order = latest_paid_order(user)
    if order is None:
        from django.utils import timezone

        from billing.models import SubscriptionOrder

        now = timezone.now()
        order = SubscriptionOrder(
            number="OWZPREVIEW01",
            plan_name="Growth (preview)",
            cycle=SubscriptionOrder.Cycle.YEARLY,
            customer_name=getattr(user, "full_name", "") or "",
            customer_email=getattr(user, "email", "") or "",
            customer_phone=getattr(user, "phone", "") or "",
            per_month_price=Decimal("1000.00"),
            months=12,
            base_amount=Decimal("11111.11"),
            discount_amount=Decimal("1111.11"),
            taxable_amount=Decimal("10000.00"),
            gst_amount=Decimal("1800.00"),
            total_amount=Decimal("11800.00"),
            status=SubscriptionOrder.Status.PAID,
            cf_order_id="OWZPREVIEW01",
            cf_payment_id="PREVIEW-PAYMENT",
            paid_at=now,
            current_period_start=now.date(),
        )
    bill = _bill_for(user, order)
    bill["is_preview"] = True
    return bill


def _bill_for(user, order) -> dict:
    from billing import gst

    entity = seller()
    profile = _profile(user)

    # ---- buyer ------------------------------------------------------------
    name = (
        (getattr(profile, "company_name", "") or "").strip()
        or (getattr(user, "full_name", "") or "").strip()
        or (order.customer_name or "").strip()
        or user.email
    )
    address_parts = [
        (getattr(profile, "address", "") or "").strip(),
        (getattr(profile, "city", "") or "").strip(),
    ]
    address = ", ".join(p for p in address_parts if p)
    phone = (
        (getattr(profile, "phone", "") or "").strip()
        or (getattr(user, "phone", "") or "").strip()
        or (order.customer_phone or "").strip()
    )
    gstin = gst.normalise_gstin(getattr(profile, "gstin", "") or "")
    # Place of supply: the buyer's GSTIN state when registered, else their state.
    state = gst.effective_state(getattr(profile, "state", "") or "", gstin)

    # ---- amounts (reference arithmetic) -------------------------------------
    paid = _money(order.total_amount)
    taxable_raw = Decimal(order.total_amount or 0) / _GST_DIVISOR
    months = int(order.months or 1) or 1
    intra = gst.is_intra_state(state, gstin, entity.get("state"), entity.get("gstin"))

    taxable = _money(taxable_raw)
    rate = _money(taxable_raw / months)
    half_gst = _money(taxable_raw * _HALF_GST)
    full_gst = _money(taxable_raw * _FULL_GST)

    base = Decimal(order.base_amount or 0)
    discount_pct = _money((Decimal(order.discount_amount or 0) / base) * 100) if base > 0 else _money(0)

    tax_rows = (
        [{"label": "CGST", "rate": "9%", "amount": half_gst},
         {"label": "SGST", "rate": "9%", "amount": half_gst}]
        if intra
        else [{"label": "IGST", "rate": "18%", "amount": full_gst}]
    )

    return {
        "order": order,
        "seller": entity,
        "client": {
            "name": name,
            "address": address,
            "phone": phone,
            "state": state,
            "gstin": gstin,
        },
        "invoice_date": order.paid_at or order.created_at,
        "invoice_number": order.number,
        "reference": order.cf_payment_id or order.cf_order_id or f"REF-{str(user.pk)[:8].upper()}",
        "plan_name": order.plan_name,
        "cycle_label": order.get_cycle_display(),
        "months": months,
        "period_start": order.current_period_start,
        "period_end": order.current_period_end,
        "paid": paid,
        "taxable": taxable,
        "rate": rate,
        "hsn": entity.get("hsn") or "998313",
        "intra_state": intra,
        "cgst": half_gst,
        "sgst": half_gst,
        "igst": full_gst,
        "tax_rows": tax_rows,
        "discount_pct": discount_pct,
        "signature": {
            "url": _signature_data_uri() or entity["signature_url"],
            "designation": entity["signatory_designation"],
        },
        "is_preview": False,
    }
