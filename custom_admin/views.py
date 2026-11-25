"""Admin sign-in for arcane.onewooz.com.

Two steps (both gated by ``AdminAccessMiddleware``):

1. **Email + password** of a staff account.
2. **A one-time code emailed to that address**, which the admin enters to finish.

Only when both pass is the session marked :data:`SESSION_ADMIN_VERIFIED`, which
the middleware requires for every non-public admin path. The OTP tooling
(:mod:`accounts.otp`) and mailer (:mod:`accounts.emails`) are shared with the
regular app so codes are issued, hashed and rate-limited identically.
"""

from __future__ import annotations

import logging

from django.contrib.auth import login as auth_login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from accounts import emails, otp
from accounts.models import User
from core import ratelimit
from core.queries import sorted_rows

from .middleware import SESSION_ADMIN_VERIFIED, SESSION_LAST_SEEN

logger = logging.getLogger("onewooz.audit")

OTP_PURPOSE = "admin_login"
PENDING_USER = "admin_login_user"
PENDING_EMAIL = "admin_login_email"


def _clear_pending(request):
    request.session.pop(PENDING_USER, None)
    request.session.pop(PENDING_EMAIL, None)


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def sign_in(request):
    """Step 1  email + password. On success, email a code and go to verify."""
    if request.method == "GET":
        return render(
            request, "custom_admin/sign_in.html", {"timed_out": "timeout" in request.GET}
        )

    email = User.objects.normalize_email((request.POST.get("email") or "").strip())
    password = request.POST.get("password") or ""
    ip = ratelimit.client_identity(request)

    # Throttle credential guessing by IP and by identifier.
    if not ratelimit.consume("login.ip", ip).allowed or (
        email and not ratelimit.consume("login.identifier", email).allowed
    ):
        return render(
            request,
            "custom_admin/sign_in.html",
            {"error": "Too many attempts. Please wait a moment and try again."},
            status=429,
        )

    user = User.objects.filter(email=email).first() if email else None
    if not (user and user.is_active and user.is_staff and user.check_password(password)):
        # Generic message: a prober learns nothing about which half was wrong.
        logger.warning("admin sign-in failed: email=%s ip=%s", email, request.META.get("REMOTE_ADDR"))
        return render(
            request,
            "custom_admin/sign_in.html",
            {"error": "Those details did not match. Please try again."},
            status=401,
        )

    # Credentials good → issue and email a one-time code, park the pending user.
    if not ratelimit.consume("otp.send.email", email).allowed:
        return render(
            request,
            "custom_admin/sign_in.html",
            {"error": "Too many codes requested. Please wait a few minutes."},
            status=429,
        )
    code = otp.issue(OTP_PURPOSE, email)
    sent = emails.send_login_code(email, code, minutes=otp.TTL_SECONDS // 60)
    if not sent:
        # The code was issued but the email could not be delivered (SMTP refusal,
        # unreachable provider, or an SES-sandbox unverified recipient). Don't
        # strand the admin on a verify page with no code  say so here.
        logger.warning("admin sign-in: OTP email send failed: email=%s", email)
        return render(
            request,
            "custom_admin/sign_in.html",
            {"error": "We couldn't send your sign-in code. Please try again shortly or contact support."},
            status=502,
        )
    request.session[PENDING_USER] = str(user.pk)
    request.session[PENDING_EMAIL] = email
    return redirect("custom_admin:sign_in_verify")


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def sign_in_verify(request):
    """Step 2  enter the emailed code. On success, establish the admin session."""
    email = request.session.get(PENDING_EMAIL)
    user_pk = request.session.get(PENDING_USER)
    if not email or not user_pk:
        return redirect("custom_admin:sign_in")

    if request.method == "GET":
        return render(request, "custom_admin/verify.html", {"email": email})

    if not ratelimit.consume("otp.verify", email).allowed:
        return render(
            request,
            "custom_admin/verify.html",
            {"email": email, "error": "Too many attempts. Start again to get a new code."},
            status=429,
        )

    result = otp.verify(OTP_PURPOSE, email, request.POST.get("code", ""))
    if not result.ok:
        return render(
            request,
            "custom_admin/verify.html",
            {"email": email, "error": "That code is not right or has expired."},
            status=401,
        )

    # Re-check the account still qualifies before granting the session.
    user = User.objects.filter(pk=user_pk, is_active=True, is_staff=True).first()
    if user is None:
        _clear_pending(request)
        return redirect("custom_admin:sign_in")

    ratelimit.reset("otp.verify", email)
    otp.invalidate(OTP_PURPOSE, email)
    _clear_pending(request)

    # login() cycles the session key (fixation defence); set the admin flags on
    # the fresh session.
    auth_login(request, user, backend="accounts.backends.EmailPasswordBackend")
    request.session[SESSION_ADMIN_VERIFIED] = True
    request.session[SESSION_LAST_SEEN] = timezone.now().isoformat()
    logger.warning("admin sign-in ok: email=%s ip=%s", email, request.META.get("REMOTE_ADDR"))
    return redirect("custom_admin:overview")


def _admin_context(request, nav: str, **extra) -> dict:
    """Context for our own admin pages, rendered inside the Django admin shell
    (custom_admin/_shell.html): admin's each_context gives the header, the
    model sidebar and permissions; ``owa_nav`` marks the active top-bar link."""
    from django.contrib import admin

    context = admin.site.each_context(request)
    context.update({"owa_nav": nav, **extra})
    return context


# Quick links on the overview: (admin changelist url name, title, description, icon).
_OVERVIEW_TOOLS = (
    ("admin:index", "Database", "Every model and table, with search, filters and bulk actions.", "db"),
    ("custom_admin:expiring_users", "Expiring users", "Owners whose paid plan ends soon, by duration.", "receipt"),
    ("custom_admin:deleted_users", "Deleted users", "Owners who deleted their account, with plan, stores and recovery date.", "users"),
    ("custom_admin:export_excel", "Export Excel", "Sales, renewal and deletion reports as formatted Excel workbooks.", "file"),
    ("mkt_console:overview", "Marketplace", "OneWooz Apps: build apps, price designs, licences, orders, health and alerts.", "tag"),
    ("ask_console:overview", "Ask OneWooz", "The community: moderation queue, bans, threads, topic rooms, AI answers and the weekly digest.", "chat"),
    ("custom_admin:email_templates", "Email templates", "Build and publish the email templates stores send.", "mail"),
    ("admin:ud_businesshubpost_changelist", "Business hub", "News, help and important posts for owners' dashboards.", "megaphone"),
    ("admin:accounts_dashboarduser_changelist", "Store owners", "Owner accounts: status, contact details, access.", "users"),
    ("admin:billing_subscriptionplan_changelist", "Plans", "Pricing plans, features, commerce fee bands and penalties.", "tag"),
    ("admin:billing_subscriptionorder_changelist", "Subscription orders", "Every plan purchase and its payment status.", "receipt"),
    ("admin:billing_subscriptioninvite_changelist", "Plan invites", "Custom-priced plan links sent to prospects.", "send"),
    ("admin:commerce_bill_changelist", "Store bills", "Commerce fee invoices and payment proofs.", "file"),
    ("admin:accounts_notification_changelist", "Notifications", "Bell notifications sent to owners.", "bell"),
    ("admin:web_customertestimonial_changelist", "Testimonials", "Customer testimonials on the marketing site.", "quote"),
    ("admin:web_legalpolicy_changelist", "Policies", "Legal policies listed in the site footer.", "shield"),
    ("admin:web_marketingcorepage_changelist", "Core pages", "Help centre and company pages on onewooz.com.", "pages"),
    ("admin:web_marketingblog_changelist", "Blog", "Posts on the onewooz.com blog.", "pen"),
)


def _overview_tools():
    tools = []
    for url_name, title, desc, icon in _OVERVIEW_TOOLS:
        try:
            url = reverse(url_name)
        except Exception:
            continue  # model not registered / app not installed
        tools.append({"url": url, "title": title, "desc": desc, "icon": icon})
    return tools


def _overview_kpis():
    """Platform-wide headline numbers. Each is best-effort: a missing table or
    a failing query shows a dash instead of breaking the page."""
    from datetime import timedelta

    from django.db.models import Sum

    since = timezone.now() - timedelta(days=30)
    kpis = []

    def add(label, fn, sub=""):
        try:
            value = fn()
        except Exception:
            value = None
        kpis.append({"label": label, "value": "–" if value is None else value, "sub": sub})

    add("Store owners", lambda: f"{User.objects.filter(is_staff=False, is_active=True, account_deleted_at__isnull=True).count():,}", "Active accounts")

    def _stores():
        from tenancy.models import Site

        return f"{Site.objects.filter(is_deleted=False).count():,}"

    add("Stores", _stores, "Live & draft")

    def _orders():
        from commerce.models import Order

        return f"{Order.objects.across_all_sites().filter(created_at__gte=since).count():,}"

    add("Store orders", _orders, "Last 30 days")

    def _subs():
        from billing.models import SubscriptionOrder

        return f"{SubscriptionOrder.objects.filter(status=SubscriptionOrder.Status.PAID, paid_at__gte=since).count():,}"

    add("Plan purchases", _subs, "Last 30 days")

    def _revenue():
        from billing.models import SubscriptionOrder

        total = (
            SubscriptionOrder.objects.filter(status=SubscriptionOrder.Status.PAID, paid_at__gte=since)
            .aggregate(t=Sum("total_amount"))["t"]
        ) or 0
        return f"₹{total:,.0f}"

    add("Plan revenue", _revenue, "Last 30 days")
    return kpis


def _recent_audit(limit: int = 14):
    try:
        from core.models import AuditEvent

        events = list(AuditEvent.objects.order_by("-created_at")[:limit])
    except Exception:
        return []
    return [
        {
            "action": e.action,
            "who": e.actor_label or e.actor_kind,
            "target": e.target_label,
            "when": timezone.localtime(e.created_at).strftime("%d %b, %H:%M"),
        }
        for e in events
    ]


def overview(request):
    return render(
        request,
        "custom_admin/overview.html",
        _admin_context(
            request,
            "overview",
            title="Overview",
            kpis=_overview_kpis(),
            tools=_overview_tools(),
            audit=_recent_audit(),
        ),
    )


# --------------------------------------------------------------------------
# Email template builder (admin-authored, ChatGPT-assisted)
# --------------------------------------------------------------------------
# Admins craft reusable marketing email templates here with a chat builder. Each
# prompt asks ChatGPT to rewrite the whole template; the preview (right) renders
# it with demo content/images so the author sees how a store's variables land.
# A template stays DRAFT  invisible to stores  until the admin marks it
# complete (publish). Everything is behind AdminAccessMiddleware already.

def _email_template_qs():
    from outreach.models import EmailTemplate

    return EmailTemplate.objects.all()


def email_templates(request):
    from outreach.models import EmailTemplate

    return render(
        request,
        "custom_admin/email_templates.html",
        _admin_context(
            request, "email", title="Email templates",
            # Rows carry full email HTML: sort keys only (MySQL error 1038).
            templates=sorted_rows(EmailTemplate.objects.all()),
        ),
    )


def _render_builder(request, template):
    from django.conf import settings

    from outreach.ai_templates import STYLE_CHOICES
    from outreach.services import rendering
    from outreach.variables import TEMPLATE_VARIABLES

    preview = rendering.preview_html(template.html) if template and template.html else ""
    return render(
        request,
        "custom_admin/email_template_builder.html",
        {
            "template": template,
            "is_edit": bool(template),
            "variables": TEMPLATE_VARIABLES,
            "initial_preview": preview,
            "openai_text_model": settings.OPENAI_TEXT_MODEL,
            "style_choices": STYLE_CHOICES,
        },
    )


@require_http_methods(["GET", "POST"])
def email_template_new(request):
    from outreach.models import EmailTemplate

    if request.method == "POST":
        return _save_email_template(request, None)
    return _render_builder(request, None)


@require_http_methods(["GET", "POST"])
def email_template_edit(request, template_id):
    from django.shortcuts import get_object_or_404

    template = get_object_or_404(_email_template_qs(), pk=template_id)
    if request.method == "POST":
        return _save_email_template(request, template)
    return _render_builder(request, template)


def _save_email_template(request, template):
    from outreach.models import EmailTemplate

    action = request.POST.get("action") or "save"
    name = (request.POST.get("name") or "").strip() or "Untitled template"
    html = request.POST.get("html") or ""
    description = (request.POST.get("description") or "").strip()

    if template is None:
        template = EmailTemplate(created_by=getattr(request.user, "email", ""))
    template.name = name
    template.description = description
    template.html = html
    ptype = (request.POST.get("product_type") or "").strip()
    if ptype in EmailTemplate.ProductType.values:
        template.product_type = ptype
    if action == "publish":
        template.status = EmailTemplate.Status.PUBLISHED
    elif action == "unpublish":
        template.status = EmailTemplate.Status.DRAFT
    template.save()

    from core import audit

    audit.record(
        "admin.email_template.published" if action == "publish" else "admin.email_template.saved",
        request=request,
        target=template,
        target_kind="EmailTemplate",
        target_label=template.name,
        actor_kind="admin",
    )
    return redirect(reverse("custom_admin:email_templates"))


@require_http_methods(["POST"])
def email_template_delete(request, template_id):
    from django.shortcuts import get_object_or_404

    template = get_object_or_404(_email_template_qs(), pk=template_id)
    template.delete()
    return redirect(reverse("custom_admin:email_templates"))


@require_http_methods(["POST"])
def email_template_generate(request):
    """AI turn: {prompt, history, html} → {ok, html, preview}. ChatGPT-backed."""
    import json

    from django.http import JsonResponse

    try:
        ratelimit.check("ai.generate", str(getattr(request.user, "id", "admin")))
    except ratelimit.RateLimitExceeded:
        return JsonResponse(
            {"ok": False, "error": "A lot of requests just now  please wait a moment."},
            status=429,
        )

    try:
        body = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    prompt = (body.get("prompt") or "").strip()
    history = body.get("history") or []
    current_html = body.get("html") or ""
    model = (body.get("model") or "").strip()
    template_type = (body.get("product_type") or "").strip()
    style = (body.get("style") or "").strip()
    images = body.get("images") or []
    files = body.get("files") or []

    from outreach import ai_templates
    from outreach.services import rendering

    result = ai_templates.generate(
        prompt,
        history=history,
        current_html=current_html,
        model=model,
        template_type=template_type,
        style=style,
        images=images,
        files=files,
    )
    if not result.get("ok"):
        return JsonResponse(result, status=502)
    html = result["html"]
    return JsonResponse({
        "ok": True,
        "html": html,
        "preview": rendering.preview_html(html),
        "model": result.get("model", ""),
    })


def email_template_preview(request, template_id):
    """Standalone demo preview of a saved template (opened in its own page)."""
    from django.http import HttpResponse
    from django.shortcuts import get_object_or_404

    from outreach.services import rendering

    template = get_object_or_404(_email_template_qs(), pk=template_id)
    return HttpResponse(rendering.preview_html(template.html or ""))


# --------------------------------------------------------------------------
# Forgot / reset password (staff accounts)
# --------------------------------------------------------------------------
# A reset link carries a purpose-bound signed token, single-use by binding a
# fingerprint of the current password hash into it  the same technique as the
# app's own reset (accounts.views), scoped here to staff and to the arcane host.

ADMIN_RESET_PURPOSE = "admin.password_reset"


def _admin_reset_fingerprint(user) -> str:
    import hashlib

    basis = f"{user.pk}:{user.password}".encode()
    return hashlib.sha256(basis).hexdigest()[:16]


def _resolve_admin_reset_token(raw: str):
    from core import tokens

    if not raw:
        return None
    try:
        payload = tokens.unsign(ADMIN_RESET_PURPOSE, raw)
    except tokens.TokenError:
        return None
    user = User.objects.filter(pk=payload.get("u"), is_active=True, is_staff=True).first()
    if user is None or not user.has_usable_password():
        return None
    if payload.get("fp") != _admin_reset_fingerprint(user):
        return None
    return user


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def forgot(request):
    """Step 1  email a reset link to a staff account (identical response either way)."""
    if request.method == "GET":
        return render(request, "custom_admin/forgot.html")

    email = User.objects.normalize_email((request.POST.get("email") or "").strip())
    ip = ratelimit.client_identity(request)
    address_ok = ratelimit.consume("password_reset.send", email).allowed if email else False
    ip_ok = ratelimit.consume("otp.send.ip", ip).allowed

    if address_ok and ip_ok and email:
        from core import tokens

        user = User.objects.filter(email=email, is_active=True, is_staff=True).first()
        # Only a real, password-capable staff account is mailed a link. Anything
        # else is treated identically, so the response discloses nothing.
        if user is not None and user.has_usable_password():
            token = tokens.sign(
                ADMIN_RESET_PURPOSE,
                {"u": str(user.pk), "fp": _admin_reset_fingerprint(user)},
            )
            url = request.build_absolute_uri(
                reverse("custom_admin:reset") + f"?t={token}"
            )
            emails.send_password_reset(
                email, url, minutes=tokens.ttl_for(ADMIN_RESET_PURPOSE) // 60
            )
            logger.warning("admin password reset requested: email=%s ip=%s", email, request.META.get("REMOTE_ADDR"))

    # Identical response  sent, not sent, or rate limited.
    return render(request, "custom_admin/forgot_sent.html", {"email": email})


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def reset(request):
    """Step 2  validate the link and set a new password."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    from core import audit

    raw = request.GET.get("t") or request.POST.get("t") or ""
    user = _resolve_admin_reset_token(raw)
    if user is None:
        return render(request, "custom_admin/reset_invalid.html", status=400)

    if request.method == "GET":
        return render(request, "custom_admin/reset.html", {"token": raw})

    new = request.POST.get("new_password") or ""
    confirm = request.POST.get("confirm_password") or ""
    errors = []
    if new != confirm:
        errors.append("The two passwords don't match.")
    try:
        validate_password(new, user=user)
    except ValidationError as exc:
        errors.extend(exc.messages)
    if errors:
        return render(
            request, "custom_admin/reset.html", {"token": raw, "errors": errors}, status=400
        )

    user.set_password(new)
    user.save(update_fields=["password", "password_changed_at", "updated_at"])
    audit.record("admin.password.reset", request=request, actor=user, actor_kind="user")
    logger.warning("admin password reset completed: email=%s ip=%s", user.email, request.META.get("REMOTE_ADDR"))
    return redirect(reverse("custom_admin:sign_in") + "?reset=1")


# --------------------------------------------------------------------------
# Excel reports (Export Excel on the admin home)
# --------------------------------------------------------------------------
# One page to pick a report (sales or renewals), an optional payment-date range
# and a couple of filters; the download is a styled .xlsx built by
# custom_admin.exports (data) and custom_admin.export_workbook (layout). The
# export carries customer PII, so it is a POST, rate limited and audit logged.


def _export_page(request, *, error="", values=None, status=200):
    from . import exports

    today = timezone.localdate()
    renewal_reports = [
        {"key": r.key, "label": r.label, "description": r.description,
         "window": exports.renewal_window(r.key, today)[2]}
        for r in exports.REPORTS if not r.uses_date_range
    ]
    return render(
        request,
        "custom_admin/export_excel.html",
        _admin_context(
            request,
            "exports",
            title="Export Excel",
            sales_reports=[r for r in exports.REPORTS if r.uses_date_range],
            renewal_reports=renewal_reports,
            retention_days=exports.retention_days(),
            today=today,
            error=error,
            values=values or {"report": "subscription_sales"},
        ),
        status=status,
    )


def _parse_date(raw: str):
    import datetime as _dt

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"“{raw}” is not a valid date.") from None


@require_http_methods(["GET", "POST"])
def export_excel(request):
    from django.http import HttpResponse

    from core import audit

    from . import export_workbook, exports

    if request.method == "GET":
        return _export_page(request)

    values = {
        "report": (request.POST.get("report") or "").strip(),
        "date_from": (request.POST.get("date_from") or "").strip(),
        "date_to": (request.POST.get("date_to") or "").strip(),
        "include_zero": request.POST.get("include_zero") == "1",
        "include_review": request.POST.get("include_review") == "1",
    }
    spec = exports.REPORTS_BY_KEY.get(values["report"])
    if spec is None:
        return _export_page(request, error="Choose a report to export.", values=values, status=400)
    try:
        date_from = _parse_date(values["date_from"]) if spec.uses_date_range else None
        date_to = _parse_date(values["date_to"]) if spec.uses_date_range else None
    except ValueError as exc:
        return _export_page(request, error=str(exc), values=values, status=400)
    if date_from and date_to and date_from > date_to:
        return _export_page(
            request, error="The start date is after the end date.", values=values, status=400
        )

    if not ratelimit.consume("admin.export", str(request.user.pk)).allowed:
        return _export_page(
            request,
            error="Too many exports in a short time. Please wait a few minutes and try again.",
            values=values,
            status=429,
        )

    report = exports.build(
        spec.key,
        date_from=date_from,
        date_to=date_to,
        include_zero=values["include_zero"],
        include_review=values["include_review"],
        request=request,
    )
    data = export_workbook.to_xlsx(report)
    name = export_workbook.filename(report, date_from, date_to)

    audit.record(
        "admin.report.exported",
        request=request,
        actor_kind="admin",
        target_kind="ExcelReport",
        target_label=spec.label,
        context={
            "report": spec.key,
            "period": report.period_label,
            "filters": report.filters,
            "records": len(report.rows),
            "file": name,
        },
    )

    response = HttpResponse(
        data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "no-store"
    # Lets the page's script know the file is ready (see export_excel.html).
    token = (request.POST.get("download_token") or "")[:40]
    if token.replace("-", "").isalnum():
        response.set_cookie("ow_export_done", token, max_age=60, secure=request.is_secure(),
                            samesite="Strict", path="/")
    return response


# --------------------------------------------------------------------------
# Deleted users / Expiring users (buttons on the admin home)
# --------------------------------------------------------------------------
# Read-only lists built by custom_admin.lifecycle, each with a duration filter
# (quick ranges plus a custom date range), search and paging. Both show owner
# contact details, so they need the "view user" permission.


def _require_user_view(request) -> None:
    from django.core.exceptions import PermissionDenied

    if not (request.user.has_perm("accounts.view_user") or request.user.has_perm("accounts.change_user")):
        raise PermissionDenied


def _search(rows, q: str):
    q = (q or "").strip().lower()
    if not q:
        return rows
    def hit(r):
        hay = " ".join([r.name or "", r.email or "", r.phone or "", r.company or ""]
                       + [s["name"] + " " + s["address"] for s in r.sites])
        return q in hay.lower()
    return [r for r in rows if hit(r)]


def _paged(request, rows, per_page=50):
    from django.core.paginator import Paginator

    return Paginator(rows, per_page).get_page(request.GET.get("page") or 1)


def _query_without_page(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


@require_http_methods(["GET"])
def deleted_users(request):
    from accounts import deletion

    from . import lifecycle

    _require_user_view(request)
    window = lifecycle.deleted_window(request.GET)
    rows = lifecycle.deleted_users(window)
    q = (request.GET.get("q") or "").strip()
    rows = _search(rows, q)
    counts = {
        "total": len(rows),
        "recoverable": sum(1 for r in rows if r.state == "recoverable"),
        "awaiting_renewal": sum(1 for r in rows if r.state == "awaiting_renewal"),
        "closed": sum(1 for r in rows if r.state == "closed"),
        "stores": sum(len(r.sites) for r in rows),
    }
    return render(
        request,
        "custom_admin/deleted_users.html",
        _admin_context(
            request,
            "deleted_users",
            title="Deleted users",
            window=window,
            ranges=lifecycle.DELETED_RANGES,
            page=_paged(request, rows),
            counts=counts,
            q=q,
            qs=_query_without_page(request),
            retention_days=deletion.retention_days(),
            today=timezone.localdate(),
        ),
    )


@require_http_methods(["GET"])
def expiring_users(request):
    from . import lifecycle

    _require_user_view(request)
    window = lifecycle.expiring_window(request.GET)
    rows = lifecycle.expiring_users(window)
    q = (request.GET.get("q") or "").strip()
    rows = _search(rows, q)
    from decimal import Decimal

    counts = {
        "total": len(rows),
        "today": sum(1 for r in rows if r.days_left == 0),
        "week": sum(1 for r in rows if r.days_left <= 7),
        "value": sum((r.amount or Decimal("0")) for r in rows),
    }
    return render(
        request,
        "custom_admin/expiring_users.html",
        _admin_context(
            request,
            "expiring_users",
            title="Expiring users",
            window=window,
            ranges=lifecycle.EXPIRING_RANGES,
            page=_paged(request, rows),
            counts=counts,
            q=q,
            qs=_query_without_page(request),
            today=timezone.localdate(),
        ),
    )
