"""Storefront customer self-service: profile, address book, email change and
account deletion, for the signed-in (email-OTP) shopper.

A shopper is a :class:`~commerce.models.Customer` keyed by email within one
store. Orders keep their own snapshot of name / email / phone / address, so
nothing here ever edits an order's details.

Deleting an account (:func:`delete_account`) is a soft delete: nothing is
removed from the database (the platform admin can still see everything), but
the shopper's data stops being theirs or visible anywhere in the store:

* the Customer row is soft-deleted and marked ``account_deleted_at``; its live
  email becomes a per-customer placeholder and the original email / phone move
  to ``deleted_email`` / ``deleted_phone``. Every existing flow that skips
  deleted customers (bulk email, bulk credit, email marketing, address
  backfill) skips it, and the owner's Customers page shows only the name with
  an "Account deleted" badge;
* saved addresses stay attached to that hidden row;
* wishlist items and store credit are re-keyed to the placeholder email (and
  credit deactivated), so they never apply or show again;
* the newsletter subscription is deactivated;
* every order is kept unchanged, and linked to the row first, so the customer
  sync can never rebuild the profile from old orders. A later order with the
  same email starts a fresh customer.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from commerce.models import Customer, CustomerAddress, Order

LABEL_HOME = "Home"
LABEL_OFFICE = "Office"
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{5,18}[0-9]$")
_PIN_RE = re.compile(r"^[A-Za-z0-9 \-]{3,10}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileError(ValueError):
    """A shopper-facing validation message."""


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalise_email(value) -> str:
    return (str(value or "")).strip().lower()


def valid_email(value) -> bool:
    value = normalise_email(value)
    return bool(value) and len(value) <= 254 and bool(_EMAIL_RE.match(value))


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def find_customer(site, email: str):
    """The live Customer row for ``email`` (owner-soft-deleted rows included,
    shopper-deleted accounts never), or None."""
    email = normalise_email(email)
    if not email:
        return None
    return (
        Customer.objects.for_site(site)
        .filter(email=email, account_deleted_at__isnull=True)
        .first()
    )


def ensure_customer(site, email: str):
    """The Customer row for ``email``, created when the shopper has none yet.
    A row the owner had removed is restored: the shopper is actively using it."""
    email = normalise_email(email)
    customer = find_customer(site, email)
    if customer is None:
        latest = (
            Order.objects.for_site(site)
            .filter(customer_email__iexact=email)
            .order_by("-created_at")
            .only("customer_name", "customer_phone")
            .first()
        )
        customer = Customer.objects.create(
            site=site,
            email=email,
            name=(latest.customer_name if latest else "")[:120],
            phone=(latest.customer_phone if latest else "")[:20],
        )
    elif customer.is_deleted:
        customer.restore()
    return customer


def account_orders(site, email: str, customer=None):
    """Every order that belongs to this shopper: placed with their email, or
    linked to their Customer row (orders placed before an email change)."""
    from django.db.models import Q

    email = normalise_email(email)
    q = Q(customer_email__iexact=email)
    if customer is None:
        customer = find_customer(site, email)
    if customer is not None:
        q |= Q(customer=customer)
    # Orders of an account the shopper deleted never come back, even if they
    # sign in again later with the same email (that is a fresh account).
    return (
        Order.objects.for_site(site)
        .filter(q)
        .filter(Q(customer__isnull=True) | Q(customer__account_deleted_at__isnull=True))
    )


# Status chips on the account's My Orders list, in the order they show.
# "returned" is not an Order status: it is an order whose shipment came back
# to the seller (Shipment RTO), see :func:`filter_orders_by_chip`.
ORDER_CHIPS = (
    ("created", "Placed"),
    ("confirmed", "Confirmed"),
    ("paid", "Paid"),
    ("shipped", "Shipped"),
    ("delivered", "Delivered"),
    ("cancelled", "Cancelled"),
    ("returned", "Returned"),
)
# Chips a phone folds into the "More" menu (the rest stay on the row).
ORDER_CHIPS_MORE = ("paid", "delivered", "cancelled", "returned")


def filter_orders_by_chip(site, qs, chip: str):
    """Narrow the shopper's orders to one status chip ("" or unknown = all).
    Returned is a subquery on shipments rather than a join + DISTINCT, which
    would make MySQL sort full Order rows (JSON columns, error 1038)."""
    from commerce.models import Shipment

    if chip == "returned":
        rto = Shipment.objects.for_site(site).filter(status=Shipment.Status.RTO)
        return qs.filter(pk__in=rto.values("order_id"))
    if chip in {c[0] for c in ORDER_CHIPS}:
        return qs.filter(status=chip)
    return qs


def _https_url(value) -> str:
    """``value`` if it is an absolute https link, else ""."""
    from urllib.parse import urlsplit

    url = str(value or "").strip()
    if not url or len(url) > 500:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme == "https" and parts.netloc else ""


def annotate_order_cards(site, orders) -> None:
    """Give each order on the account page what its card shows:

    * ``is_returned``: a shipment came back to the seller (RTO);
    * ``track_url``: the courier's tracking page, for **shipped** orders only
      (the Track Order button opens it in a new tab). From the live
      shipment's stored link, else the latest courier event's ``track_url``,
      else Shiprocket's public tracking page for its AWB. Empty when there is
      no https link to send the shopper to, which hides the button.

    Two narrow queries for the whole page (plus one for events, only when a
    shipped order has no stored link)."""
    from commerce.models import CourierWebhookEvent, Shipment

    for o in orders:
        o.is_returned = False
        o.track_url = ""
    if not orders:
        return
    by_id = {o.pk: o for o in orders}
    rows = list(
        Shipment.objects.for_site(site)
        .filter(order_id__in=list(by_id))
        .order_by()
        .values("order_id", "status", "tracking_url", "awb", "provider__provider", "created_at")
    )
    latest = {}
    for r in rows:
        if r["status"] == Shipment.Status.RTO:
            by_id[r["order_id"]].is_returned = True
        if r["status"] == Shipment.Status.CANCELLED:
            continue
        cur = latest.get(r["order_id"])
        if cur is None or (r["created_at"] and cur["created_at"] and r["created_at"] > cur["created_at"]):
            latest[r["order_id"]] = r

    shipped = [o for o in orders if o.status == Order.Status.SHIPPED]
    missing = []
    for o in shipped:
        sh = latest.get(o.pk)
        o.track_url = _https_url(sh["tracking_url"]) if sh else ""
        if not o.track_url:
            missing.append(o)
    if missing:
        # Latest event per order: pick ids on narrow columns first, then read
        # only those payloads (never sort rows that carry the JSON).
        newest = {}
        for e in (
            CourierWebhookEvent.objects.for_site(site)
            .filter(order_id__in=[o.pk for o in missing])
            .order_by()
            .values("pk", "order_id", "received_at")
        ):
            cur = newest.get(e["order_id"])
            if cur is None or (e["received_at"] and cur["received_at"] and e["received_at"] > cur["received_at"]):
                newest[e["order_id"]] = e
        payloads = {}
        if newest:
            payloads = dict(
                CourierWebhookEvent.objects.for_site(site)
                .filter(pk__in=[e["pk"] for e in newest.values()])
                .order_by()
                .values_list("order_id", "raw_payload")
            )
        for o in missing:
            payload = payloads.get(o.pk)
            if isinstance(payload, dict):
                o.track_url = _https_url(payload.get("track_url"))
            if not o.track_url:
                sh = latest.get(o.pk)
                awb = ((sh["awb"] if sh else "") or o.tracking_id or "").strip()
                if sh and sh["provider__provider"] == "shiprocket" and awb and re.fullmatch(r"[A-Za-z0-9\-]{4,40}", awb):
                    o.track_url = f"https://shiprocket.co/tracking/{awb}"


def addresses(site, customer) -> list:
    """Saved addresses, default first, then most recently changed."""
    if customer is None:
        return []
    rows = list(CustomerAddress.objects.for_site(site).filter(customer=customer).order_by())
    rows.sort(key=lambda a: (not a.is_default, -(a.updated_at.timestamp() if a.updated_at else 0)))
    return rows


def address_label(address) -> str:
    return address.label or (
        "Billing" if address.address_type == CustomerAddress.AddressType.BILLING else "Address"
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
def save_profile(site, email: str, data) -> Customer:
    name = _clean(data.get("name"), 120)
    phone = _clean(data.get("phone"), 20)
    if not name:
        raise ProfileError("Please enter your name.")
    if phone and not _PHONE_RE.match(phone):
        raise ProfileError("Please enter a valid mobile number.")
    customer = ensure_customer(site, email)
    customer.name = name
    customer.phone = phone
    customer.save(update_fields=["name", "phone", "updated_at"])
    return customer


def check_new_email(site, current: str, new: str) -> str:
    """The normalised new email, or a ProfileError explaining why not."""
    new = normalise_email(new)
    if not valid_email(new):
        raise ProfileError("Please enter a valid email address.")
    if new == normalise_email(current):
        raise ProfileError("That is already your email.")
    if Customer.objects.for_site(site).filter(email=new).exists():
        raise ProfileError("That email is already used by another account on this store.")
    return new


def change_email(site, current: str, new: str) -> Customer:
    """Move the shopper to ``new`` (already proven with a code). Orders keep the
    email they were placed with and stay theirs through the Customer link;
    store credit and wishlist follow the account."""
    from commerce.models import CustomerCredit, WishlistItem

    current = normalise_email(current)
    new = check_new_email(site, current, new)
    with transaction.atomic():
        customer = ensure_customer(site, current)
        Order.all_objects.filter(
            site=site, customer_email__iexact=current, customer__isnull=True
        ).update(customer=customer)
        customer.email = new
        customer.save(update_fields=["email", "updated_at"])
        CustomerCredit.objects.for_site(site).filter(customer_email__iexact=current).update(
            customer_email=new
        )
        wishlist = WishlistItem.objects.for_site(site)
        already = set(wishlist.filter(customer_email=new).values_list("product_id", flat=True))
        if already:
            wishlist.filter(customer_email=current, product_id__in=already).delete()
        wishlist.filter(customer_email=current).update(customer_email=new)
    return customer


# ---------------------------------------------------------------------------
# Address book
# ---------------------------------------------------------------------------
def save_address(site, email: str, data, address_id=None) -> CustomerAddress:
    choice = (data.get("label_choice") or "").strip().lower()
    if choice == "home":
        label = LABEL_HOME
    elif choice == "office":
        label = LABEL_OFFICE
    else:
        label = _clean(data.get("label_custom"), 40)
        if not label:
            raise ProfileError("Give this address a name, for example Home or Office.")
    address_type = (data.get("address_type") or "").strip()
    if address_type not in CustomerAddress.AddressType.values:
        address_type = CustomerAddress.AddressType.BOTH

    house = _clean(data.get("house_no"), 120)
    street = _clean(data.get("street_area"), 200)
    city = _clean(data.get("city"), 120)
    state = _clean(data.get("state"), 120)
    pincode = _clean(data.get("pincode"), 10)
    phone = _clean(data.get("phone"), 20)
    if not (house or street):
        raise ProfileError("Please enter the house / flat and street.")
    if not city:
        raise ProfileError("Please enter the city.")
    if not pincode or not _PIN_RE.match(pincode):
        raise ProfileError("Please enter a valid PIN code.")
    if phone and not _PHONE_RE.match(phone):
        raise ProfileError("Please enter a valid phone number for this address.")
    make_default = str(data.get("is_default") or "").lower() in ("1", "true", "on", "yes")

    customer = ensure_customer(site, email)
    book = CustomerAddress.objects.for_site(site).filter(customer=customer)
    with transaction.atomic():
        if address_id:
            address = book.filter(pk=address_id).first()
            if address is None:
                raise ProfileError("That address no longer exists.")
        else:
            address = CustomerAddress(site=site, customer=customer)
            make_default = make_default or not book.exists()
        address.label = label
        address.address_type = address_type
        address.house_no = house
        address.street_area = street
        address.city = city
        address.state = state
        address.pincode = pincode
        address.phone = phone
        address.address_text = ", ".join(p for p in (house, street) if p)
        if make_default:
            address.is_default = True
        address.save()
        if make_default:
            book.exclude(pk=address.pk).filter(is_default=True).update(is_default=False)
    return address


def delete_address(site, email: str, address_id) -> bool:
    customer = find_customer(site, email)
    if customer is None:
        return False
    book = CustomerAddress.objects.for_site(site).filter(customer=customer)
    address = book.filter(pk=address_id).first()
    if address is None:
        return False
    was_default = address.is_default
    with transaction.atomic():
        address.delete()
        if was_default:
            nxt = sorted(book, key=lambda a: a.updated_at or timezone.now(), reverse=True)
            if nxt:
                book.filter(pk=nxt[0].pk).update(is_default=True)
    return True


# ---------------------------------------------------------------------------
# Delete account
# ---------------------------------------------------------------------------
def placeholder_email(customer) -> str:
    """The live email a deleted account is parked under (unique per customer,
    on a reserved TLD that can never receive mail or be signed in with)."""
    return f"deleted-{customer.pk.hex}@account-deleted.invalid"


def delete_account(site, email: str):
    """Close the shopper's account: hide their data everywhere in the store but
    keep every row in the database (see the module docstring). Returns the
    Customer row."""
    from commerce.models import CustomerCredit, NewsletterSubscriber, WishlistItem

    email = normalise_email(email)
    now = timezone.now()
    with transaction.atomic():
        customer = ensure_customer(site, email)
        parked = placeholder_email(customer)
        # Link every order of this email first, so the customer sync (which only
        # looks at unlinked orders) can never recreate the profile from them.
        Order.all_objects.filter(
            site=site, customer_email__iexact=email, customer__isnull=True
        ).update(customer=customer)
        # Saved addresses stay on the (now hidden) customer row.
        WishlistItem.objects.for_site(site).filter(customer_email=email).update(
            customer_email=parked
        )
        CustomerCredit.objects.for_site(site).filter(customer_email__iexact=email).update(
            customer_email=parked, is_active=False, updated_at=now
        )
        NewsletterSubscriber.objects.for_site(site).filter(
            email__iexact=email, is_active=True
        ).update(is_active=False, updated_at=now)
        # A closed account gets no more cart reminders.
        from commerce.models import AbandonedCart

        AbandonedCart.objects.for_site(site).filter(customer_email=email).delete()
        customer.deleted_email = email
        customer.deleted_phone = customer.phone or ""
        customer.email = parked
        customer.phone = ""
        customer.account_deleted_at = now
        customer.is_deleted = True
        customer.deleted_at = now
        customer.save(update_fields=[
            "email", "phone", "deleted_email", "deleted_phone",
            "account_deleted_at", "is_deleted", "deleted_at", "updated_at",
        ])
    return customer
