"""Pre-filling the storefront checkout form for a returning shopper.

Two sources, never mixed across shoppers:

* **A signed-in customer** (email proven by OTP, see
  :mod:`storefront.customer_accounts`): their Customer row in this store, their
  saved delivery address, and failing that the details on their latest order.
* **This browser's last checkout on this store**: what the shopper typed the
  last time they placed an order here, remembered in their own session
  (:func:`remember`). Nothing is looked up by a typed email, so a visitor can
  never pull up somebody else's address.

Every value is only a starting point: the form stays editable and the server
validates what is finally submitted.
"""

from __future__ import annotations

FIELDS = (
    "name", "phone", "email",
    "house", "street", "notes", "city", "state", "pincode",
    "company_name", "gstin",
)

_SESSION_KEY = "ow_checkout_last"


def _blank() -> dict:
    return {k: "" for k in FIELDS}


def _clean(value, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _split_street(address: str) -> tuple[str, str]:
    from commerce.customer_sync import _split_street as split

    return split(address)


def _remembered(request, site) -> dict:
    try:
        stash = request.session.get(_SESSION_KEY) or {}
    except Exception:
        return {}
    entry = stash.get(str(site.pk)) if isinstance(stash, dict) else None
    return entry if isinstance(entry, dict) else {}


def _fill_gaps(data: dict, source: dict) -> None:
    for key in FIELDS:
        if not data.get(key) and source.get(key):
            data[key] = _clean(source[key], 500 if key == "notes" else 200)


def _from_account(site, email: str) -> dict:
    """The signed-in shopper's details on file in this store."""
    from commerce.models import Customer, CustomerAddress, Order

    out = _blank()
    out["email"] = email
    customer = (
        Customer.objects.for_site(site).filter(email__iexact=email, is_deleted=False).first()
    )
    if customer is not None:
        out["name"] = customer.name or ""
        out["phone"] = customer.phone or ""
        address = (
            CustomerAddress.objects.for_site(site)
            .filter(customer=customer)
            .exclude(address_type=CustomerAddress.AddressType.BILLING)
            .order_by("-is_default", "-updated_at")
            .first()
        )
        if address is not None:
            house, street = address.house_no, address.street_area
            if not (house or street) and address.address_text:
                house, street = _split_street(address.address_text)
            out.update({
                "house": house or "",
                "street": street or "",
                "city": address.city or "",
                "state": address.state or "",
                "pincode": address.pincode or "",
            })
            if not out["phone"]:
                out["phone"] = address.phone or ""
            if address.has_gst or address.gstin:
                out["company_name"] = address.company_name or ""
                out["gstin"] = address.gstin or ""

    # Whatever is still missing comes from their most recent order here.
    order = (
        Order.objects.for_site(site)
        .filter(customer_email__iexact=email)
        .order_by("-created_at")
        .first()
    )
    if order is not None:
        house, street = _split_street(order.shipping_address)
        _fill_gaps(out, {
            "name": order.customer_name,
            "phone": order.customer_phone,
            "house": house,
            "street": street,
            "city": order.shipping_city,
            "state": order.shipping_state,
            "pincode": order.shipping_pincode,
            "company_name": order.company_name,
            "gstin": order.gstin,
        })
    return out


def prefill_for(request, site) -> dict:
    """Values to pre-fill the checkout form with (blank strings when unknown)."""
    from .customer_accounts import current_customer_email

    data = _blank()
    try:
        email = current_customer_email(request)
        remembered = _remembered(request, site)
        # The last checkout typed in this browser is the freshest, but only for
        # the same shopper when someone is signed in.
        if remembered and (
            not email or _clean(remembered.get("email")).lower() == email
        ):
            _fill_gaps(data, remembered)
        if email:
            data["email"] = email
            _fill_gaps(data, _from_account(site, email))
    except Exception:
        # A prefill is a convenience; checkout must render without it.
        import logging

        logging.getLogger("onewooz.audit").exception("checkout prefill failed site=%s", site.pk)
        return _blank()
    data["phone"] = "".join(ch for ch in data["phone"] if ch.isdigit())[-10:] if data["phone"] else ""
    data["pincode"] = "".join(ch for ch in data["pincode"] if ch.isdigit())[:6] if data["pincode"] else ""
    data["gstin"] = data["gstin"].upper()
    return data


_ADDRESS_FIELDS = ("house", "street", "city", "state", "pincode")


def _address_option(address) -> dict:
    """One saved address as the checkout's address pickers need it."""
    house, street = address.house_no or "", address.street_area or ""
    if not (house or street) and address.address_text:
        house, street = _split_street(address.address_text)
    phone = "".join(ch for ch in (address.phone or "") if ch.isdigit())[-10:]
    pincode = "".join(ch for ch in (address.pincode or "") if ch.isdigit())[:6]
    line = ", ".join(p for p in (house, street, address.city) if p)
    if pincode:
        line = f"{line} {pincode}".strip()
    gst = bool(address.has_gst or address.gstin)
    return {
        "id": str(address.pk),
        "label": (address.label or "").strip() or (address.city or "Saved address"),
        "type": address.address_type,
        "is_default": bool(address.is_default),
        "house": _clean(house), "street": _clean(street),
        "city": _clean(address.city), "state": _clean(address.state),
        "pincode": pincode, "phone": phone, "line": _clean(line, 300),
        "has_gst": gst,
        "company_name": _clean(address.company_name) if gst else "",
        "gstin": (address.gstin or "").upper()[:15] if gst else "",
    }


def address_book(request, site, prefill: dict) -> dict:
    """The signed-in shopper's saved addresses for the checkout pickers, and
    which one each block starts on.

    The checkout's main address block is the billing address (also the
    delivery address unless "Ship to a different address" is ticked), so it
    lists addresses saved for Billing or Shipping & billing; the
    ship-to-different block lists Shipping or Shipping & billing. Each block
    starts on its default (the account's default address first, then the most
    recently changed), written into ``prefill`` so the fields arrive filled in.
    A billing-only default with a separate shipping address opens the
    ship-to-different block already filled. Empty when signed out or nothing is
    saved, and the checkout then behaves exactly as before."""
    from .customer_accounts import current_customer_email

    empty = {"billing": [], "shipping": [], "billing_id": "", "shipping_id": "",
             "ship_diff": False, "ship": {}}
    try:
        email = current_customer_email(request)
        if not email:
            return empty
        from commerce.models import Customer, CustomerAddress

        customer = (
            Customer.objects.for_site(site).filter(email__iexact=email, is_deleted=False).first()
        )
        if customer is None:
            return empty
        rows = list(CustomerAddress.objects.for_site(site).filter(customer=customer).order_by())
        rows.sort(key=lambda a: (not a.is_default, -(a.updated_at.timestamp() if a.updated_at else 0)))
        options = [_address_option(a) for a in rows]
        T = CustomerAddress.AddressType
        billing = [o for o in options if o["type"] in (T.BILLING, T.BOTH)]
        shipping = [o for o in options if o["type"] in (T.SHIPPING, T.BOTH)]
        if not options:
            return empty
        main = billing[0] if billing else shipping[0]
        ship = shipping[0] if shipping else None
        ship_diff = bool(main["type"] == T.BILLING and ship is not None and ship["id"] != main["id"])

        # The fields arrive filled with the chosen address (a saved address wins
        # over what this browser typed last time).
        for key in _ADDRESS_FIELDS:
            prefill[key] = main[key]
        if main["phone"] and not prefill.get("phone"):
            prefill["phone"] = main["phone"]
        if main["has_gst"]:
            prefill["company_name"], prefill["gstin"] = main["company_name"], main["gstin"]
        return {
            "billing": billing,
            "shipping": shipping,
            "billing_id": main["id"] if main in billing else "",
            "shipping_id": ship["id"] if ship else "",
            "ship_diff": ship_diff,
            "ship": ship if ship_diff else {},
        }
    except Exception:
        import logging

        logging.getLogger("onewooz.audit").exception("checkout address book failed site=%s", site.pk)
        return empty


def remember(request, site, values: dict) -> None:
    """Keep what the shopper just typed at checkout, in their own session, so
    their next checkout on this store starts filled in."""
    try:
        values = dict(values)
        if not (values.get("house") or values.get("street")) and values.get("address"):
            # A checkout that posts one address line (the referral store's).
            values["house"], values["street"] = _split_street(values["address"])
        entry = {k: _clean(values.get(k), 500 if k == "notes" else 200) for k in FIELDS}
        if not any(entry.values()):
            return
        stash = request.session.get(_SESSION_KEY)
        stash = dict(stash) if isinstance(stash, dict) else {}
        stash[str(site.pk)] = entry
        # Bound the session payload: keep the most recent few stores only.
        if len(stash) > 5:
            for key in list(stash)[:-5]:
                stash.pop(key, None)
        request.session[_SESSION_KEY] = stash
        request.session.modified = True
    except Exception:
        pass


# ── Saving checkout addresses to the shopper's address book ────────────────

def _digits(value, limit: int) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:limit]


def _book_entry(house, street, city, state, pincode, phone) -> dict | None:
    """A complete address as the address book stores it, or ``None`` when it
    is missing a part (house/street, city, a 6-digit PIN)."""
    entry = {
        "house": _clean(house, 120),
        "street": _clean(street, 200),
        "city": _clean(city, 120),
        "state": _clean(state, 120),
        "pincode": _digits(pincode, 6),
        "phone": _digits(phone, 15),
    }
    if not (entry["house"] or entry["street"]) or not entry["city"] or len(entry["pincode"]) != 6:
        return None
    return entry


def _norm(value) -> str:
    return " ".join(str(value or "").split()).lower()


def _same_address(row, entry: dict) -> bool:
    """Whether the saved ``row`` is the address in ``entry`` (case, spacing
    and the saved one-line form are ignored)."""
    if _norm(row.city) != _norm(entry["city"]) or _digits(row.pincode, 6) != entry["pincode"]:
        return False
    if _norm(row.house_no) == _norm(entry["house"]) and _norm(row.street_area) == _norm(entry["street"]):
        return True
    line = ", ".join(p for p in (entry["house"], entry["street"]) if p)
    return bool(row.address_text) and _norm(row.address_text) == _norm(line)


def save_to_address_book(request, site, post) -> int:
    """Save the addresses a signed-in shopper used at checkout to their
    address book, unless they are already in it. Returns how many were added.

    The main block is saved as *Shipping & billing* (or *Billing* when the
    order ships elsewhere, with the GST details typed there); the ship-to-
    different block as *Shipping*. An address already saved is not duplicated,
    though one saved only for billing (or only for shipping) that is now used
    for the other becomes *Shipping & billing*. The first address saved
    becomes the default. Guests have no address book, so nothing is saved for
    them. Best-effort: never raises, so it can't fail an order.
    """
    import logging

    try:
        from commerce.models import CustomerAddress

        from .account_profile import ensure_customer
        from .customer_accounts import current_customer_email

        email = current_customer_email(request)
        if not email:
            return 0
        T = CustomerAddress.AddressType
        ship_diff = str(post.get("ship_diff") or "") == "1"
        blocks = []
        main = _book_entry(
            post.get("house"), post.get("street"),
            post.get("bill_city"), post.get("bill_state"), post.get("bill_pincode"),
            post.get("bill_phone") or post.get("phone"),
        )
        if main is not None:
            gstin = _clean(post.get("gstin"), 15).upper()
            main["gstin"] = gstin
            main["company_name"] = _clean(post.get("company_name"), 200) if gstin else ""
            blocks.append((main, T.BILLING if ship_diff else T.BOTH))
        if ship_diff:
            ship = _book_entry(
                post.get("ship_house"), post.get("ship_street"),
                post.get("ship_city"), post.get("ship_state"), post.get("ship_pincode"),
                post.get("ship_phone"),
            )
            if ship is not None:
                ship["gstin"], ship["company_name"] = "", ""
                blocks.append((ship, T.SHIPPING))
        if not blocks:
            return 0

        customer = ensure_customer(site, email)
        added = 0
        for entry, kind in blocks:
            rows = list(CustomerAddress.objects.for_site(site).filter(customer=customer).order_by())
            match = next((r for r in rows if _same_address(r, entry)), None)
            if match is not None:
                if match.address_type not in (kind, T.BOTH):
                    match.address_type = T.BOTH
                    match.save(update_fields=["address_type", "updated_at"])
                continue
            CustomerAddress.objects.create(
                site=site,
                customer=customer,
                address_type=kind,
                has_gst=bool(entry["gstin"]),
                company_name=entry["company_name"],
                gstin=entry["gstin"],
                phone=entry["phone"],
                house_no=entry["house"],
                street_area=entry["street"],
                city=entry["city"],
                state=entry["state"],
                pincode=entry["pincode"],
                address_text=", ".join(p for p in (entry["house"], entry["street"]) if p),
                is_default=not rows,
            )
            added += 1
        return added
    except Exception:
        logging.getLogger("onewooz.audit").exception("saving checkout address failed site=%s", site.pk)
        return 0
