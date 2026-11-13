"""Keep the Customer directory in step with orders.

Checkout only snapshots a shopper's name / email / phone onto the order  it
never created a :class:`~commerce.models.Customer` row. So a store could have
plenty of orders yet an empty Customers page. This module closes that gap:

* :func:`upsert_customer_for_order` turns the shopper behind one order into a
  first-class Customer (keyed by lower-cased email within the store) and links
  the order to them. Called from checkout for every new order.
* :func:`sync_customers_for_site` heals historical orders placed before this
  existed, so opening the Customers page backfills everyone who has ordered.

Both are idempotent: a shopper who already has a Customer row is reused, not
duplicated, and a linked order is left alone.

The delivery address the shopper typed at checkout is saved onto the Customer
as a :class:`~commerce.models.CustomerAddress` too (:func:`save_order_address`),
so the owner sees it on the Customers page. An address already on file is not
duplicated; the first one becomes the default.
"""

from __future__ import annotations


def upsert_customer_for_order(order, site=None):
    """Ensure the shopper who placed ``order`` exists as a Customer and is
    linked to the order. Returns the Customer, or ``None`` when the order
    carries no email to key on (e.g. a bare COD order without contact details).
    """
    email = (order.customer_email or "").strip().lower()
    if not email:
        return None

    site = site or order.site
    customer, _created = site.customers.get_or_create(
        email=email,
        defaults={
            "name": order.customer_name or "",
            "phone": order.customer_phone or "",
        },
    )

    # A shopper who orders again "un-deletes"  and fill in any detail the
    # customer row was missing but this order carries.
    changed = False
    if getattr(customer, "is_deleted", False):
        customer.is_deleted = False
        customer.deleted_at = None
        changed = True
    if not customer.name and order.customer_name:
        customer.name = order.customer_name
        changed = True
    if not customer.phone and order.customer_phone:
        customer.phone = order.customer_phone
        changed = True
    if changed:
        customer.save()

    if order.customer_id != customer.pk:
        order.customer = customer
        order.save(update_fields=["customer", "updated_at"])

    try:
        save_order_address(order, customer, site=site)
    except Exception:
        # The address is a convenience copy; it must never fail an order.
        import logging

        logging.getLogger("onewooz.audit").exception(
            "saving customer address failed: order=%s", order.pk
        )

    return customer


def _norm(value) -> str:
    return " ".join(str(value or "").split()).lower()


def _split_street(address: str) -> tuple[str, str]:
    """Checkout sends one line built as ``house, street, notes``; split the
    house number off the front so the saved address keeps its parts."""
    text = " ".join(str(address or "").split())
    if not text:
        return "", ""
    head, sep, rest = text.partition(",")
    if not sep:
        return "", text
    return head.strip()[:120], rest.strip()[:200]


def save_order_address(order, customer, site=None):
    """Save the order's delivery address on ``customer`` (once).

    Returns the :class:`~commerce.models.CustomerAddress` (new or the matching
    one already on file), or ``None`` when the order carries no address.
    """
    from commerce.models import CustomerAddress

    address = (order.shipping_address or "").strip()
    city = (order.shipping_city or "").strip()
    pincode = (order.shipping_pincode or "").strip()
    if not (address or city or pincode):
        return None
    site = site or order.site
    house_no, street_area = _split_street(address)
    state = (order.shipping_state or "").strip()

    existing = CustomerAddress.objects.for_site(site).filter(customer=customer)
    for a in existing:
        # The order's address line is "house, street, delivery notes", so an
        # address saved in parts (from checkout or the account page) matches
        # when the line starts with its house + street.
        parts = _norm(", ".join(p for p in (a.house_no, a.street_area) if p))
        same = (
            _norm(a.pincode) == _norm(pincode)
            and _norm(a.city) == _norm(city)
            and (
                _norm(a.address_text) == _norm(address)
                or (_norm(a.house_no) == _norm(house_no) and _norm(a.street_area) == _norm(street_area))
                or (parts and (_norm(address) == parts or _norm(address).startswith(parts + ",")))
            )
        )
        if same:
            return a

    gstin = (order.gstin or "").strip().upper()[:15]
    # A billing address that differs from the delivery one means the shopper
    # shipped elsewhere: this address is for shipping only.
    billing = (order.billing_address or "").strip()
    addr_type = (
        CustomerAddress.AddressType.SHIPPING
        if billing and _norm(billing) != _norm(address)
        else CustomerAddress.AddressType.BOTH
    )
    return CustomerAddress.objects.create(
        site=site,
        customer=customer,
        address_type=addr_type,
        has_gst=bool(gstin),
        company_name=(order.company_name or "")[:200],
        gstin=gstin,
        phone=(order.customer_phone or "")[:20],
        house_no=house_no,
        street_area=street_area,
        city=city[:120],
        state=state[:120],
        pincode=pincode[:10],
        address_text=address,
        is_default=not existing.exists(),
    )


def sync_customers_for_site(site) -> int:
    """Backfill Customer rows for every order that has an email but no linked
    customer. Cheap once healed  the filtered query then matches nothing."""
    from commerce.models import Order

    unlinked = (
        Order.objects.for_site(site)
        .filter(customer__isnull=True)
        .exclude(customer_email="")
        .order_by("created_at")
    )
    count = 0
    for order in unlinked.iterator():
        if upsert_customer_for_order(order, site=site) is not None:
            count += 1

    # Customers made before addresses were saved: copy the address from their
    # latest order. Matches nothing once healed.
    from commerce.models import Customer

    missing = (
        Customer.objects.for_site(site)
        .filter(is_deleted=False, addresses__isnull=True)
        .filter(orders__shipping_address__gt="")
        .distinct()
    )
    for customer in missing[:200]:
        order = (
            Order.objects.for_site(site)
            .filter(customer=customer)
            .exclude(shipping_address="")
            .order_by("-created_at")
            .first()
        )
        if order is not None:
            try:
                save_order_address(order, customer, site=site)
            except Exception:
                pass
    return count
