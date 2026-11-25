"""Admin Excel reports: what OneWooz sold, and whose subscription is ending.

Ten reports, all built from records the platform already keeps:

Sales (filtered by payment date):

* **Subscription sales**  paid :class:`billing.SubscriptionOrder` rows.
* **Template sales**  paid website designs: legacy
  :class:`billing.TemplatePurchase` rows plus marketplace orders for the
  *Templates* app (:data:`marketplace.registry.TEMPLATES_SLUG`).
* **Plugin sales**  paid marketplace orders for every other app.
* **Commerce fee sales**  commerce fee bills stores paid
  (:class:`commerce.Bill`), with links to the payment screenshot and the bill's
  invoice PDF, and the reference the owner quoted.

Renewals (relative to today, owner by owner):

* **Expiring today / this week / this month**  the owner's subscription ends
  today, by Sunday, or by the last day of this month, and nothing later has been
  bought. "This week" and "this month" start today: anything already past is in
  *Expired*.
* **Expired**  the owner's latest paid subscription has ended and nothing
  replaced it (the same rule as the renewal wall, :mod:`billing.renewal`).
* **Deletion today / in 7 days**  expired owners whose data-deletion date
  (plan end + ``settings.SUBSCRIPTION_DATA_RETENTION_DAYS``) is today, or in the
  next seven days.

Every row carries the same columns (buyer, tax identity, websites, what was
bought, the money and the GST split), so the workbook writer
(:mod:`custom_admin.export_workbook`) lays out every report the same way.

Money
-----
All arithmetic is ``Decimal`` rounded half-up to paise. The GST split follows
the documents the buyer actually received:

* subscriptions use the subscription bill's arithmetic (:mod:`billing.bill`):
  ``taxable = paid / (1 + rate)``, CGST and SGST each half the rate on the
  taxable value for a buyer in the seller's state, IGST otherwise;
* templates and plugins use the amounts frozen on the order (base + GST), split
  the same way;
* commerce fee bills use the GST breakup stored on the bill (``meta["tax"]``),
  exactly as printed on its invoice.

"Others" is whatever the total holds beyond the taxable value and GST (paise
rounding on a bill, normally 0.00), so on every row
``paid amount + CGST + SGST + IGST + others = total amount paid`` holds exactly.

Queries never ask MySQL to sort wide rows (``Site.content``, ``Bill.meta``):
orderings are cleared and rows are sorted in Python (see ``core.queries``).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_TWO = Decimal("0.01")
_ZERO = Decimal("0.00")

def money(value) -> Decimal:
    return Decimal(value or 0).quantize(_TWO, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Report catalogue
# --------------------------------------------------------------------------

SALES = "sales"
RENEWALS = "renewals"


@dataclass(frozen=True)
class ReportSpec:
    key: str
    label: str
    group: str  # SALES | RENEWALS
    description: str

    @property
    def uses_date_range(self) -> bool:
        return self.group == SALES

    @property
    def is_commerce(self) -> bool:
        return self.key == "commerce_fee_sales"


REPORTS: tuple[ReportSpec, ...] = (
    ReportSpec(
        "subscription_sales", "Subscription sales", SALES,
        "Every paid subscription plan purchase, with the GST split exactly as on the owner's bill.",
    ),
    ReportSpec(
        "template_sales", "Template sales", SALES,
        "Paid website designs: direct template purchases and the Templates app on the marketplace.",
    ),
    ReportSpec(
        "plugin_sales", "Plugin sales", SALES,
        "Paid marketplace apps (every app except website templates).",
    ),
    ReportSpec(
        "commerce_fee_sales", "Commerce fee sales", SALES,
        "Commerce fee bills stores paid, with the payment screenshot, the original file and the reference number.",
    ),
    ReportSpec(
        "expiring_this_week", "Expiring this week", RENEWALS,
        "Owners whose paid subscription ends between today and Sunday, with nothing renewed yet.",
    ),
    ReportSpec(
        "expiring_this_month", "Expiring this month", RENEWALS,
        "Owners whose paid subscription ends between today and the last day of this month, with nothing renewed yet.",
    ),
    ReportSpec(
        "expiring_today", "Expiring today", RENEWALS,
        "Owners whose paid subscription ends today, with nothing renewed yet.",
    ),
    ReportSpec(
        "expired", "Expired", RENEWALS,
        "Owners whose latest paid subscription has ended and has not been renewed.",
    ),
    ReportSpec(
        "deletion_today", "Deletion today", RENEWALS,
        "Expired owners whose store data reaches its deletion date today.",
    ),
    ReportSpec(
        "deletion_7_days", "Deletion in 7 days", RENEWALS,
        "Expired owners whose store data reaches its deletion date in the next 7 days (tomorrow onwards).",
    ),
)
REPORTS_BY_KEY = {r.key: r for r in REPORTS}


def retention_days() -> int:
    """Days a lapsed owner's data is kept after the plan ends."""
    try:
        return max(0, int(getattr(settings, "SUBSCRIPTION_DATA_RETENTION_DAYS", 30)))
    except (TypeError, ValueError):
        return 30


def renewal_window(key: str, today: dt.date) -> tuple[dt.date | None, dt.date | None, str]:
    """(first, last, words) of the date a renewal report filters on.

    Expiring reports filter the plan end date; deletion reports the deletion
    date; *Expired* has no window (every lapsed owner).
    """
    fmt = lambda d: d.strftime("%d %b %Y")  # noqa: E731
    if key == "expiring_today":
        return today, today, f"Plan ends on {fmt(today)}"
    if key == "expiring_this_week":
        end = today + dt.timedelta(days=6 - today.weekday())
        return today, end, f"Plan ends {fmt(today)} to {fmt(end)}"
    if key == "expiring_this_month":
        nxt = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        end = nxt - dt.timedelta(days=1)
        return today, end, f"Plan ends {fmt(today)} to {fmt(end)}"
    if key == "deletion_today":
        return today, today, f"Data deletion on {fmt(today)}"
    if key == "deletion_7_days":
        first, last = today + dt.timedelta(days=1), today + dt.timedelta(days=7)
        return first, last, f"Data deletion {fmt(first)} to {fmt(last)}"
    return None, None, f"Plan ended before {fmt(today)}"


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


@dataclass
class ReportRow:
    date: dt.date | None = None
    number: str = ""
    name: str = ""
    email: str = ""
    mobile: str = ""
    gstin: str = ""
    company: str = ""
    websites: str = ""
    product_type: str = ""
    item: str = ""
    taxable: Decimal = _ZERO
    cgst: Decimal = _ZERO
    sgst: Decimal = _ZERO
    igst: Decimal = _ZERO
    others: Decimal = _ZERO
    city: str = ""
    state: str = ""
    duration: str = ""
    total: Decimal = _ZERO
    # Renewal reports
    period_end: dt.date | None = None
    days: int | None = None  # days left (>= 0) or days since expiry (< 0)
    deletion_on: dt.date | None = None
    standing: str = ""
    # Commerce fee sales
    payment_status: str = ""
    reference: str = ""
    photo_url: str = ""       # the payment screenshot the owner uploaded
    attachment_url: str = ""  # the bill's invoice PDF
    sort_key: tuple = field(default_factory=tuple)

    @property
    def gst(self) -> Decimal:
        return self.cgst + self.sgst + self.igst


@dataclass
class Report:
    spec: ReportSpec
    rows: list[ReportRow]
    period_label: str
    filters: list[str]
    generated_at: dt.datetime
    generated_by: str
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Buyer directory (bulk lookups by email / site)
# --------------------------------------------------------------------------


def _clean(value) -> str:
    return " ".join(str(value or "").split())


class _Directory:
    """Owner details for a set of emails, loaded in a handful of queries."""

    def __init__(self, emails, request=None):
        from django.db.models.functions import Lower

        from accounts.models import User
        from billing.models import OnboardingProfile
        from tenancy.models import Site

        self.request = request
        wanted = {(e or "").strip().lower() for e in emails if (e or "").strip()}
        self.users: dict[str, object] = {}
        self.profiles: dict[object, object] = {}
        self.sites: dict[object, list] = {}
        if not wanted:
            return
        wanted_list = list(wanted)
        users = []
        for i in range(0, len(wanted_list), 500):
            chunk = wanted_list[i:i + 500]
            users.extend(
                User.objects.annotate(ow_email_lc=Lower("email")).filter(ow_email_lc__in=chunk).order_by()
            )
        for user in users:
            self.users.setdefault(user.email.strip().lower(), user)
        user_ids = [u.pk for u in self.users.values()]
        if not user_ids:
            return
        for profile in OnboardingProfile.objects.filter(user_id__in=user_ids).order_by():
            self.profiles[profile.user_id] = profile
        sites = (
            Site.objects.filter(owner_id__in=user_ids, is_deleted=False)
            .only("id", "owner_id", "name", "slug", "custom_domain", "custom_domain_verified",
                  "legal_name", "gstin", "created_at")
            .order_by()
        )
        for site in sites:
            self.sites.setdefault(site.owner_id, []).append(site)
        for lst in self.sites.values():
            lst.sort(key=lambda s: (s.created_at, s.name.lower()))

    def user(self, email):
        return self.users.get((email or "").strip().lower())

    def profile(self, user):
        return self.profiles.get(getattr(user, "pk", None)) if user is not None else None

    def owner_sites(self, user) -> list:
        return self.sites.get(getattr(user, "pk", None), []) if user is not None else []

    def site_address(self, site) -> str:
        if site.custom_domain and site.custom_domain_verified:
            return site.custom_domain.strip().lower()
        try:
            from core import links

            origin = links.storefront_origin(site.slug, self.request)
        except Exception:
            origin = site.slug
        return origin.split("://", 1)[-1]

    def websites(self, sites) -> str:
        return "\n".join(f"{_clean(s.name)} ({self.site_address(s)})" for s in sites)

    def party(self, email, *, fallback_name="", fallback_phone="") -> dict:
        """Buyer columns for an owner identified by ``email``."""
        from billing import gst

        user = self.user(email)
        profile = self.profile(user)
        sites = self.owner_sites(user)
        site_gstin = next((gst.normalise_gstin(s.gstin) for s in sites if (s.gstin or "").strip()), "")
        gstin = gst.normalise_gstin(getattr(profile, "gstin", "")) or site_gstin
        company = _clean(getattr(profile, "company_name", "")) or next(
            (_clean(s.legal_name) for s in sites if (s.legal_name or "").strip()), ""
        )
        return {
            "name": _clean(getattr(user, "full_name", "")) or _clean(fallback_name),
            "email": (email or "").strip(),
            "mobile": _clean(getattr(profile, "phone", "")) or _clean(getattr(user, "phone", ""))
            or _clean(fallback_phone),
            "gstin": gstin,
            "company": company,
            "websites": self.websites(sites),
            "city": _clean(getattr(profile, "city", "")),
            "state": gst.effective_state(getattr(profile, "state", "") or "", gstin),
        }


# --------------------------------------------------------------------------
# GST helpers
# --------------------------------------------------------------------------


def _platform_seller() -> dict:
    """The seller on subscription / template / plugin invoices."""
    try:
        from billing.bill import seller

        return seller()
    except Exception:  # invoice-details table missing: settings defaults only
        from billing import gst

        base = dict(getattr(settings, "PLATFORM_BILLING_ENTITY", {}) or {})
        gstin = gst.normalise_gstin(base.get("gstin") or "")
        return {"gstin": gstin, "state": gst.effective_state(base.get("state") or "", gstin)}


def _intra(party: dict, seller: dict) -> bool:
    from billing import gst

    return gst.is_intra_state(party.get("state", ""), party.get("gstin", ""),
                              seller.get("state", ""), seller.get("gstin", ""))


def _split(gst_total: Decimal, intra: bool) -> tuple[Decimal, Decimal, Decimal]:
    """(CGST, SGST, IGST) of a GST total. Halves always add back to the total."""
    gst_total = money(gst_total)
    if not gst_total:
        return _ZERO, _ZERO, _ZERO
    if intra:
        cgst = money(gst_total / 2)
        return cgst, gst_total - cgst, _ZERO
    return _ZERO, _ZERO, gst_total


def _apply_amounts(row: ReportRow, *, taxable, cgst, sgst, igst, total) -> None:
    row.taxable, row.cgst, row.sgst, row.igst = money(taxable), money(cgst), money(sgst), money(igst)
    row.total = money(total)
    row.others = row.total - row.taxable - row.cgst - row.sgst - row.igst


def _subscription_amounts(order, intra: bool) -> dict:
    """The subscription bill's arithmetic (billing.bill), at the order's rate."""
    total = Decimal(order.total_amount or 0)
    rate = Decimal(order.gst_rate or 0)
    if rate > 0 and total > 0:
        taxable_raw = total / (Decimal(1) + rate / Decimal(100))
        if intra:
            half = money(taxable_raw * rate / Decimal(200))
            cgst, sgst, igst = half, half, _ZERO
        else:
            cgst, sgst, igst = _ZERO, _ZERO, money(taxable_raw * rate / Decimal(100))
    else:
        taxable_raw, cgst, sgst, igst = total, _ZERO, _ZERO, _ZERO
    return {"taxable": taxable_raw, "cgst": cgst, "sgst": sgst, "igst": igst, "total": total}


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _d(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _local_date(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _subscription_duration(order) -> str:
    months = int(order.months or 0)
    label = order.get_cycle_display() if order.cycle else ""
    head = f"{label} ({_plural(months, 'month')})" if label and months else (label or _plural(months, "month"))
    if order.current_period_start and order.current_period_end:
        return f"{head}: {_d(order.current_period_start)} to {_d(order.current_period_end)}"
    return head


def _subscription_type(order) -> str:
    plan = getattr(order, "plan", None)
    if plan is not None:
        try:
            return f"Subscription ({plan.get_plan_type_display()} plan)"
        except Exception:
            pass
    return "Subscription"


def _subscription_item(order) -> str:
    name = _clean(order.plan_name) or "Subscription plan"
    code = _clean(order.discount_code).upper()
    if code:
        off = money(order.discount_amount)
        return f"{name} | Code {code} (-₹{off:,.2f})" if off else f"{name} | Code {code}"
    return name


def _date_bounds(date_from, date_to):
    """Aware [start, end) datetimes for a local date range (either side open)."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(date_from, dt.time.min), tz) if date_from else None
    end = (
        timezone.make_aware(dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min), tz)
        if date_to else None
    )
    return start, end


def _in_range(when, start, end) -> bool:
    if when is None:
        return False
    return (start is None or when >= start) and (end is None or when < end)


# --------------------------------------------------------------------------
# Sales reports
# --------------------------------------------------------------------------


def _subscription_row(order, directory: _Directory, seller: dict) -> ReportRow:
    party = directory.party(order.customer_email, fallback_name=order.customer_name,
                            fallback_phone=order.customer_phone)
    row = ReportRow(
        date=_local_date(order.paid_at or order.created_at),
        number=order.number,
        product_type=_subscription_type(order),
        item=_subscription_item(order),
        duration=_subscription_duration(order),
        **{k: party[k] for k in ("name", "email", "mobile", "gstin", "company", "websites", "city", "state")},
    )
    _apply_amounts(row, **_subscription_amounts(order, _intra(party, seller)))
    row.sort_key = (order.paid_at or order.created_at, order.number)
    return row


def subscription_sales(start, end, *, include_zero: bool, request=None) -> list[ReportRow]:
    from billing.models import SubscriptionOrder

    qs = SubscriptionOrder.objects.filter(status=SubscriptionOrder.Status.PAID).select_related("plan").order_by()
    if start:
        qs = qs.filter(paid_at__gte=start)
    if end:
        qs = qs.filter(paid_at__lt=end)
    if not include_zero:
        qs = qs.filter(total_amount__gt=0)
    orders = list(qs)
    directory = _Directory([o.customer_email for o in orders], request)
    seller = _platform_seller()
    return [_subscription_row(o, directory, seller) for o in orders]


def _licence_duration(kind: str, period_days, expires_at) -> str:
    kind = (kind or "").lower()
    until = f" (until {_d(_local_date(expires_at))})" if expires_at else ""
    if kind in ("permanent", "one_time"):
        return "Lifetime (permanent licence)"
    if kind == "yearly" or period_days == 365:
        return f"1 year{until}"
    if kind == "monthly" or period_days == 30:
        return f"1 month{until}"
    if kind == "quarterly":
        return f"3 months{until}"
    if kind == "half_yearly":
        return f"6 months{until}"
    if period_days:
        return f"{_plural(int(period_days), 'day')}{until}"
    return (kind.replace("_", " ").capitalize() or "Licence") + until


def _market_orders(start, end, *, templates: bool, include_zero: bool) -> list:
    from marketplace.models import MarketOrder, MarketOrderLine
    from marketplace.registry import TEMPLATES_SLUG

    qs = MarketOrder.objects.filter(status=MarketOrder.Status.PAID).order_by()
    if start:
        qs = qs.filter(paid_at__gte=start)
    if end:
        qs = qs.filter(paid_at__lt=end)
    if not include_zero:
        qs = qs.filter(total_amount__gt=0)
    orders = {o.pk: o for o in qs}
    if not orders:
        return []
    from marketplace.models import Licence

    lines: dict = {}
    line_qs = (
        MarketOrderLine.objects.filter(order_id__in=list(orders))
        .select_related("app", "item").order_by()
    )
    all_lines = list(line_qs)
    # When each line's licence runs out (one query, not one per line).
    expiry = dict(
        Licence.objects.filter(order_line_id__in=[ln.pk for ln in all_lines])
        .order_by().values_list("order_line_id", "expires_at")
    )
    for line in all_lines:
        line.licence_expires_at = expiry.get(line.pk)
        lines.setdefault(line.order_id, []).append(line)
    out = []
    for pk, order in orders.items():
        order_lines = sorted(lines.get(pk, []), key=lambda ln: ln.created_at)
        if not order_lines:
            continue
        is_template = order_lines[0].app.slug == TEMPLATES_SLUG
        if is_template == templates:
            out.append((order, order_lines))
    return out


def _market_licence_expiry(lines):
    return next((ln.licence_expires_at for ln in lines if getattr(ln, "licence_expires_at", None)), None)


def _market_row(order, lines, directory, seller, *, product_type) -> ReportRow:
    party = directory.party(order.owner_email)
    first = lines[0]
    item = "; ".join(_clean(ln.description) for ln in lines if _clean(ln.description))
    if product_type == "Plugin (app)":
        category = getattr(first.app, "get_category_display", lambda: "")()
        product = f"Plugin (app): {category}" if category else "Plugin (app)"
    else:
        product = "Website template (marketplace)"
    row = ReportRow(
        date=_local_date(order.paid_at or order.created_at),
        number=order.number,
        product_type=product,
        item=item,
        duration=_licence_duration(first.licence_type, first.period_days, _market_licence_expiry(lines)),
        **{k: party[k] for k in ("name", "email", "mobile", "gstin", "company", "websites", "city", "state")},
    )
    cgst, sgst, igst = _split(order.gst_amount, _intra(party, seller))
    _apply_amounts(row, taxable=order.base_amount, cgst=cgst, sgst=sgst, igst=igst, total=order.total_amount)
    row.sort_key = (order.paid_at or order.created_at, order.number)
    return row


def template_sales(start, end, *, include_zero: bool, request=None) -> list[ReportRow]:
    from billing.models import TemplatePurchase

    qs = TemplatePurchase.objects.filter(status=TemplatePurchase.Status.PAID).order_by()
    if start:
        qs = qs.filter(paid_at__gte=start)
    if end:
        qs = qs.filter(paid_at__lt=end)
    if not include_zero:
        qs = qs.filter(total_amount__gt=0)
    purchases = list(qs)
    market = _market_orders(start, end, templates=True, include_zero=include_zero)
    directory = _Directory([p.email for p in purchases] + [o.owner_email for o, _ in market], request)
    seller = _platform_seller()

    rows = []
    for p in purchases:
        party = directory.party(p.email)
        row = ReportRow(
            date=_local_date(p.paid_at or p.created_at),
            number=p.number,
            product_type="Website template",
            item=_clean(p.template_name) or p.template_key,
            duration=_licence_duration(p.license_type, None, p.expires_at),
            **{k: party[k] for k in ("name", "email", "mobile", "gstin", "company", "websites", "city", "state")},
        )
        cgst, sgst, igst = _split(p.gst_amount, _intra(party, seller))
        _apply_amounts(row, taxable=p.base_amount, cgst=cgst, sgst=sgst, igst=igst, total=p.total_amount)
        row.sort_key = (p.paid_at or p.created_at, p.number)
        rows.append(row)
    rows.extend(
        _market_row(o, lines, directory, seller, product_type="Website template")
        for o, lines in market
    )
    return rows


def plugin_sales(start, end, *, include_zero: bool, request=None) -> list[ReportRow]:
    market = _market_orders(start, end, templates=False, include_zero=include_zero)
    directory = _Directory([o.owner_email for o, _ in market], request)
    seller = _platform_seller()
    return [_market_row(o, lines, directory, seller, product_type="Plugin (app)") for o, lines in market]


# -- commerce fee ------------------------------------------------------------


def _bill_tax(bill) -> dict:
    """The GST breakup printed on the bill (read-only: never re-prices it)."""
    raw = (bill.meta or {}).get("tax") or {}
    total = money(bill.total_amount)
    if raw:
        def dec(key):
            try:
                return money(Decimal(str(raw.get(key) or 0)))
            except Exception:
                return _ZERO

        return {"taxable": dec("subtotal"), "cgst": dec("cgst"), "sgst": dec("sgst"),
                "igst": dec("igst"), "total": total}
    # Issued before GST was added: the whole amount is the fee, no tax.
    return {"taxable": total, "cgst": _ZERO, "sgst": _ZERO, "igst": _ZERO, "total": total}


def _absolute(url: str, request) -> str:
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _file_url(field_file, request) -> str:
    """Absolute URL of a stored file, or "" when there is none.

    Only the stored name is turned into a URL: the file itself is never
    downloaded, so the export stays fast whatever the bills hold.
    """
    if not field_file:
        return ""
    try:
        return _absolute(field_file.url, request)
    except Exception:
        logger.warning("commerce fee export: no URL for %s", getattr(field_file, "name", ""), exc_info=True)
        return ""


def commerce_fee_sales(start, end, *, include_review: bool, request=None) -> tuple[list[ReportRow], list[str]]:
    from accounts.models import User
    from commerce.models import Bill, Warehouse
    from tenancy.models import Site

    statuses = [Bill.Status.PAID] + ([Bill.Status.PENDING_REVIEW] if include_review else [])
    # Bill rows carry ``meta`` (JSON): no ORDER BY in the database.
    bills = list(Bill.objects.across_all_sites().filter(status__in=statuses).order_by())

    def paid_when(bill):
        if bill.paid_at:
            return bill.paid_at
        submitted = (bill.meta or {}).get("payment_submitted_at")
        if submitted:
            try:
                when = dt.datetime.fromisoformat(str(submitted))
                return when if timezone.is_aware(when) else timezone.make_aware(when)
            except ValueError:
                pass
        return bill.updated_at

    bills = [b for b in bills if (start is None and end is None) or _in_range(paid_when(b), start, end)]
    if not bills:
        return [], []

    site_ids = {b.site_id for b in bills}
    sites = {
        s.pk: s
        for s in Site.objects.filter(pk__in=site_ids)
        .only("id", "owner_id", "name", "slug", "custom_domain", "custom_domain_verified",
              "legal_name", "gstin", "created_at")
        .order_by()
    }
    owners = {u.pk: u for u in User.objects.filter(pk__in={s.owner_id for s in sites.values()}).order_by()}
    directory = _Directory([u.email for u in owners.values()], request)
    warehouses: dict = {}
    for wh in Warehouse.objects.across_all_sites().filter(site_id__in=site_ids).only(
        "id", "site_id", "city", "state", "is_default", "name"
    ):
        current = warehouses.get(wh.site_id)
        if current is None or (wh.is_default and not current.is_default):
            warehouses[wh.site_id] = wh

    from billing import gst

    rows, notes = [], []
    for bill in bills:
        site = sites.get(bill.site_id)
        owner = owners.get(site.owner_id) if site is not None else None
        email = getattr(owner, "email", "") or ""
        party = directory.party(email) if email else {
            "name": "", "email": "", "mobile": "", "gstin": "", "company": "", "websites": "",
            "city": "", "state": "",
        }
        wh = warehouses.get(bill.site_id)
        site_gstin = gst.normalise_gstin(getattr(site, "gstin", "")) if site is not None else ""
        gstin = site_gstin or party["gstin"]
        tax = _bill_tax(bill)
        meta = bill.meta or {}
        # A bill covers period_start up to the moment it was generated
        # (period_end), the same dates its invoice prints.
        period_days = (
            max(1, round((bill.period_end - bill.period_start).total_seconds() / 86400))
            if bill.period_start and bill.period_end else 0
        )
        row = ReportRow(
            date=_local_date(paid_when(bill)),
            number=bill.invoice_number or f"BILL-{str(bill.pk)[:8].upper()}",
            name=party["name"],
            email=party["email"],
            mobile=party["mobile"],
            gstin=gstin,
            company=(_clean(getattr(site, "legal_name", "")) if site is not None else "") or party["company"],
            websites=directory.websites([site]) if site is not None else "",
            product_type="Commerce fee",
            item=f"Commerce fee: {_plural(int(bill.total_orders or 0), 'delivered order')}",
            city=_clean(getattr(wh, "city", "")) or party["city"],
            state=gst.effective_state(getattr(wh, "state", "") or "", gstin) or party["state"],
            duration=(
                f"{_plural(period_days, 'day')}: {_d(_local_date(bill.period_start))} to "
                f"{_d(_local_date(bill.period_end))}" if period_days else ""
            ),
            payment_status=bill.get_status_display(),
            reference=_clean(meta.get("payment_reference")),
        )
        _apply_amounts(row, **tax)
        row.photo_url = _file_url(bill.payment_screenshot, request)
        row.attachment_url = _file_url(bill.invoice_pdf, request)
        row.sort_key = (paid_when(bill), row.number)
        rows.append(row)
    return rows, notes


# --------------------------------------------------------------------------
# Renewal reports
# --------------------------------------------------------------------------


def _is_paid_subscription(order) -> bool:
    """Same rule as billing.renewal: a free grant is never "due for renewal"."""
    plan = getattr(order, "plan", None)
    if plan is not None:
        return bool(plan.is_purchasable)
    return (order.total_amount or Decimal("0")) > 0


def _latest_subscriptions() -> list:
    """Each owner's subscription with the furthest end date (paid plans only)."""
    from billing.models import SubscriptionOrder

    settled = SubscriptionOrder.objects.filter(
        status=SubscriptionOrder.Status.PAID, current_period_end__isnull=False,
    ).order_by()
    best: dict[str, tuple] = {}
    for pk, email, end, created in settled.values_list(
        "pk", "customer_email", "current_period_end", "created_at"
    ):
        key = (email or "").strip().lower()
        if not key:
            continue
        current = best.get(key)
        if current is None or (end, created) > (current[1], current[2]):
            best[key] = (pk, end, created)
    if not best:
        return []
    ids = [v[0] for v in best.values()]
    orders = []
    for i in range(0, len(ids), 500):
        orders.extend(
            SubscriptionOrder.objects.filter(pk__in=ids[i:i + 500]).select_related("plan").order_by()
        )
    return [o for o in orders if _is_paid_subscription(o)]


def renewal_rows(key: str, today: dt.date, *, request=None) -> list[ReportRow]:
    keep_days = retention_days()
    first, last, _words = renewal_window(key, today)
    chosen = []
    for order in _latest_subscriptions():
        end = order.current_period_end
        deletion_on = end + dt.timedelta(days=keep_days)
        if key.startswith("expiring_"):
            ok = first <= end <= last
        elif key == "expired":
            ok = end < today
        else:  # deletion reports: lapsed and the deletion date in the window
            ok = end < today and first <= deletion_on <= last
        if ok:
            chosen.append((order, deletion_on))

    directory = _Directory([o.customer_email for o, _ in chosen], request)
    seller = _platform_seller()
    rows = []
    for order, deletion_on in chosen:
        row = _subscription_row(order, directory, seller)
        end = order.current_period_end
        row.period_end = end
        row.days = (end - today).days
        row.deletion_on = deletion_on
        if end >= today:
            row.standing = "Ends today" if end == today else f"Ends in {_plural(row.days, 'day')}"
        else:
            to_delete = (deletion_on - today).days
            if to_delete > 0:
                row.standing = f"Expired; data deleted in {_plural(to_delete, 'day')}"
            elif to_delete == 0:
                row.standing = "Expired; data deletion due today"
            else:
                row.standing = f"Expired; deletion date passed {_plural(-to_delete, 'day')} ago"
        row.sort_key = (deletion_on if key.startswith("deletion_") else end, row.email.lower())
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build(key: str, *, date_from=None, date_to=None, include_zero=False, include_review=False,
          request=None) -> Report:
    """The rows and labels for report ``key`` (a :data:`REPORTS` key)."""
    spec = REPORTS_BY_KEY[key]
    now = timezone.localtime()
    today = now.date()
    filters: list[str] = []
    notes: list[str] = []

    if spec.uses_date_range:
        start, end = _date_bounds(date_from, date_to)
        if date_from and date_to:
            period = f"Paid {_d(date_from)} to {_d(date_to)}"
        elif date_from:
            period = f"Paid on or after {_d(date_from)}"
        elif date_to:
            period = f"Paid on or before {_d(date_to)}"
        else:
            period = "All time"
        if key == "subscription_sales":
            rows = subscription_sales(start, end, include_zero=include_zero, request=request)
        elif key == "template_sales":
            rows = template_sales(start, end, include_zero=include_zero, request=request)
        elif key == "plugin_sales":
            rows = plugin_sales(start, end, include_zero=include_zero, request=request)
        else:
            rows, notes = commerce_fee_sales(start, end, include_review=include_review, request=request)
        if spec.is_commerce:
            filters.append("Paid bills and bills awaiting payment review" if include_review else "Paid bills only")
        else:
            filters.append("Including ₹0 orders" if include_zero else "Paid orders above ₹0")
        rows.sort(key=lambda r: r.sort_key)
    else:
        rows = renewal_rows(key, today, request=request)
        _f, _l, period = renewal_window(key, today)
        filters.append("Paid and one-time plans; free plans never expire into a renewal")
        filters.append(f"Data deletion date = plan end + {_plural(retention_days(), 'day')}")
        rows.sort(key=lambda r: r.sort_key)

    user = getattr(request, "user", None)
    return Report(
        spec=spec,
        rows=rows,
        period_label=period,
        filters=filters,
        generated_at=now,
        generated_by=(getattr(user, "email", "") or "") if user is not None else "",
        notes=notes,
    )
