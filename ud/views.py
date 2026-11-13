from __future__ import annotations

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from accounts import emails, otp
from commerce import activity as store_activity
from commerce.context import active_site_id, resume_url
from commerce.models import (
    AnalyticsSession,
    Enquiry,
    Order,
    PaymentGatewayConfig,
    Product,
    ShippingProvider,
)
from core import audit, ratelimit
from core.queries import paginate_sorted, sorted_rows
from core.models import AuditEvent
from storefront.content_seed import default_content
from tenancy.models import BusinessCategory, BusinessSubCategory, Site


def _counts_by_site(queryset) -> dict:
    """``{site_id: row count}`` for ``queryset``, grouped on the indexed FK."""
    return {
        row["site_id"]: row["n"]
        for row in queryset.order_by().values("site_id").annotate(n=Count("pk"))
    }


@login_required
def dashboard(request):
    """The owner's home  account overview across all their stores."""
    sites = list(request.user.sites.filter(is_deleted=False))
    # Per-store product/order counts come from two small GROUP BY queries rather
    # than COUNT(DISTINCT) annotations on the Site query. Annotating both joins
    # multiplied every store row by products × orders and forced MySQL to group
    # (sort) whole Site rows, large content JSON included, which ran it out of
    # sort memory (error 1038) once a store had real data.
    site_ids = [s.id for s in sites]
    product_counts = _counts_by_site(
        Product.all_objects.filter(site_id__in=site_ids, is_deleted=False)
    )
    # Order.all_objects skips the default manager, so exclude orders still
    # waiting on an online payment here: they do not exist yet for the owner.
    order_counts = _counts_by_site(
        Order.all_objects.filter(site_id__in=site_ids).exclude(
            status=Order.Status.AWAITING_PAYMENT
        )
    )
    for s in sites:
        s.products_count = product_counts.get(s.id, 0)
        s.orders_count = order_counts.get(s.id, 0)
    has_store = bool(sites)
    # Storefront links must carry the dev path prefix when running single-host,
    # which only a request can supply  so resolve them here, not in the template.
    for s in sites:
        s.preview_url = s.storefront_url(request)

    # The profile (store) the owner last worked on, so they can resume it. Each
    # store is an isolated profile; switching between them is explicit. The
    # choice is remembered on the account, so it persists across logout/login.
    active_id = active_site_id(request)
    active_site = next((s for s in sites if str(s.id) == active_id), None)

    # Aggregate KPIs across every store, last 30 days.
    since = timezone.now() - timedelta(days=30)
    revenue = Decimal("0")
    orders = enquiries = 0
    conversion = None
    if sites:
        span = Order.objects.for_sites(sites).filter(created_at__gte=since)
        # Every real order that came in: only cancelled and archived orders never
        # became sales. Revenue is the value of exactly this set  so a placed
        # order that isn't marked "paid" yet (a COD order, or one just confirmed)
        # contributes its value here, instead of showing under "Orders" while
        # "Revenue" stays ₹0. Revenue and Orders now count the same orders.
        live = span.exclude(
            status__in=[Order.Status.CANCELLED, Order.Status.ARCHIVED]
        )
        orders = live.count()
        revenue = live.aggregate(t=Sum("total"))["t"] or Decimal("0")
        # Conversion still uses genuinely-paid orders over visits.
        paid = span.filter(
            status__in=[Order.Status.PAID, Order.Status.SHIPPED, Order.Status.DELIVERED]
        )
        enquiries = (
            Enquiry.objects.for_sites(sites)
            .filter(is_deleted=False, created_at__gte=since)
            .count()
        )
        # Conversion = paid orders ÷ storefront visits, across all stores. Only
        # meaningful once there are visits; stays blank ("") until then.
        sessions = (
            AnalyticsSession.objects.for_sites(sites)
            .filter(started_at__gte=since)
            .count()
        )
        if sessions:
            conversion = round(paid.count() / sessions * 100, 1)

    # The enquiries KPI links through to the active store's inbox (or the first
    # store, if none is active)  enquiries live per store, so there is no
    # combined page to point at.
    enquiries_site = active_site or (sites[0] if sites else None)
    enquiries_url = (
        reverse("store:enquiries", args=[enquiries_site.id]) if enquiries_site else None
    )

    kpis = [
        {"label": "Revenue", "value": f"₹{revenue:,.0f}" if has_store else None, "caption": "Last 30 days"},
        {"label": "Orders", "value": orders if has_store else None, "caption": "Last 30 days"},
        {"label": "Enquiries", "value": enquiries if has_store else None, "caption": "Last 30 days", "url": enquiries_url},
        {"label": "Stores", "value": len(sites) if has_store else None, "caption": "Published & draft"},
        {"label": "Conversion", "value": f"{conversion:g}%" if conversion is not None else None, "caption": "Paid orders / visits"},
    ]

    # Getting started reflects the store the owner is on (the active one, else
    # their first). Each step's "done" is read from real state, so the checklist
    # tracks actual progress rather than a fixed script.
    focus = active_site or (sites[0] if sites else None)
    steps = _getting_started_steps(focus, has_store)
    current_step = next(
        (i + 1 for i, s in enumerate(steps) if s["state"] == "now"), len(steps)
    )

    # Recent activity for the focused store  the running record of everything
    # done in it (content edits, products, orders, domain, payments…), read back
    # from the audit trail as a friendly timeline rather than a raw log.
    activity = _recent_activity(focus)

    return render(
        request,
        "ud/dashboard.html",
        {
            "nav_active": "overview",
            "sites": sites,
            "active_site": active_site,
            "resume_url": resume_url(request, active_site) if active_site else "",
            "focus_site": focus,
            "kpis": kpis,
            "steps": steps,
            "current_step": current_step,
            # Every setup step finished → the "Getting started" card is replaced
            # by the AI assistant (image + text generation).
            "gs_complete": bool(steps) and all(s["state"] == "done" for s in steps),
            "ai_enabled": _ai_available(),
            "activity": activity,
        },
    )


def _ai_available() -> bool:
    """True when at least one AI backend is configured (keys in env)."""
    from django.conf import settings

    return bool(getattr(settings, "XAI_API_KEY", "") or getattr(settings, "OPENAI_API_KEY", ""))


@login_required
@require_POST
def notifications_read(request):
    """Mark the owner's notifications read (all, or one by ``id``).

    Answers a ``fetch`` (from the bell opening) with JSON, and a plain form POST
    with a redirect back  so it works with and without JavaScript.
    """
    from accounts.models import Notification

    qs = Notification.objects.filter(user=request.user, is_read=False)
    one = request.POST.get("id")
    if one:
        qs = qs.filter(id=one)
    qs.update(is_read=True, read_at=timezone.now())

    if request.headers.get("X-Requested-With") == "fetch" or _wants_json(request):
        return JsonResponse({"ok": True})
    return redirect(request.META.get("HTTP_REFERER") or reverse("ud:dashboard"))


@login_required
def notifications_unread(request):
    """Current unread count  polled by the bell to keep its badge fresh."""
    from accounts.models import Notification

    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({"unread": count})


def _wants_json(request) -> bool:
    return "application/json" in (request.headers.get("Accept") or "")


@login_required
@require_POST
def ai_chat(request):
    """The dashboard AI assistant: text (Grok) or image (OpenAI) generation.

    Both providers are called server-side (see :mod:`ud.ai`); the browser only
    ever talks to this same-origin endpoint, so no key is exposed and no CSP host
    is opened. Available to any signed-in owner (the UI only surfaces it once the
    getting-started checklist is complete).
    """
    from ud import ai

    identity = f"user:{request.user.pk}"
    if not ratelimit.consume("ai.generate", identity).allowed:
        return JsonResponse(
            {"ok": False, "error": "You’ve reached the assistant limit for now. Try again later."},
            status=429,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {}
    message = (payload.get("message") or request.POST.get("message") or "").strip()
    mode = (payload.get("mode") or request.POST.get("mode") or "text").strip().lower()
    if not message:
        return JsonResponse({"ok": False, "error": "Please type a message."}, status=400)

    if mode == "image":
        result = ai.generate_image(message)
        result.setdefault("type", "image")
    else:
        result = ai.generate_text(message)
        result.setdefault("type", "text")
    return JsonResponse(result, status=200 if result.get("ok") else 502)


def _getting_started_steps(focus, has_store):
    """The six onboarding steps, each with a real 'done' state and a CTA.

    States are assigned left-to-right: every completed step is ``done``, the
    first incomplete one is ``now`` (and carries the action button), and the
    rest are ``todo``  so the checklist always points at the single next thing.
    """
    from django.urls import reverse

    def _url(name):
        return reverse(f"store:{name}", args=[focus.id]) if focus else reverse("ud:create_site")

    has_products = bool(focus and getattr(focus, "products_count", 0))
    domain_done = bool(focus and getattr(focus, "custom_domain_verified", False))
    has_orders = bool(focus and getattr(focus, "orders_count", 0))

    pay_ok = ship_ok = False
    if focus is not None:
        pc = PaymentGatewayConfig.objects.for_site(focus).first()
        pay_ok = bool(pc and ((pc.key_id and pc.key_secret) or getattr(pc, "cod_enabled", False)))
        ship_ok = ShippingProvider.objects.for_site(focus).exists()
    payship_done = pay_ok and ship_ok

    spec = [
        ("Create your account", "Your OneWooz account is ready and secured.", True, None, None),
        ("Build your store", "Answer a few questions and we publish a working storefront.", has_store, reverse("ud:create_site"), "Start"),
        ("Add your products", "Import a catalogue or add items one by one, with photos and stock.", has_products, _url("products"), "Add products"),
        ("Connect your domain", "Point your own domain at OneWooz  we handle the certificate.", domain_done, _url("domain"), "Connect"),
        ("Configure your payment and shipping", "Set up how you get paid and how orders are delivered.", payship_done, _url("payments"), "Configure"),
        ("Start selling", "Take payments, fulfil orders and track everything from here.", has_orders, _url("overview"), "Open store"),
    ]

    steps, now_used = [], False
    for title, desc, done, cta_url, cta_label in spec:
        if done:
            state = "done"
        elif not now_used:
            state, now_used = "now", True
        else:
            state = "todo"
        steps.append(
            {
                "title": title,
                "desc": desc,
                "state": state,
                "cta_url": cta_url,
                "cta_label": cta_label,
            }
        )
    return steps


def _ago(dt):
    """Compact relative time for the activity feed  '5m ago', '2h ago', '3d ago'."""
    now = timezone.now()
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "Just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 604800:
        return f"{secs // 86400}d ago"
    return timezone.localtime(dt).strftime("%d %b")


def _recent_activity(focus, limit=60):
    """Read the focused store's audit trail back as a friendly timeline."""
    if focus is None:
        return []
    events = list(
        AuditEvent.objects.filter(site_id_ref=str(focus.id)).order_by("-created_at")[:limit]
    )
    feed = []
    for e in events:
        icon, text = store_activity.describe(e.action)
        if e.actor_kind == "customer":
            actor = "A shopper"
        elif e.actor_kind == "user":
            actor = e.actor_label or "You"
        else:
            actor = ""
        feed.append(
            {
                "when": _ago(e.created_at),
                # Exact local date + time for the row, e.g. "14 Sep 2026, 4:54 PM".
                "when_exact": timezone.localtime(e.created_at).strftime("%d %b %Y, %I:%M %p"),
                "icon": icon,
                "text": text,
                "subject": e.target_label,
                "actor": actor,
            }
        )
    return feed


@login_required
def upgrade(request):
    """In-dashboard plan upgrade  change plan without leaving the dashboard.

    The counterpart to the marketing pricing page, for an owner who is already
    signed in. It shows their current plan and every plan they can move onto, and
    runs the purchase through the *same* authenticated in-dashboard checkout the
    renewal popup uses (:mod:`billing.renewal_views`, mounted at
    ``billing_renew``)  the owner's own account email, a recomputed amount, and
    confirm-by-fetch. This is why an "Upgrade" link anywhere in the dashboard
    lands here instead of bouncing out to the public pricing site.

    A free-plan owner reaches this freely; an owner whose paid plan has lapsed is
    intercepted earlier by the renewal wall (which offers the same plans), so
    they never see this page while locked.
    """
    from billing import cashfree, renewal, subscriptions

    sub = subscriptions.current_for_owner(request.user)
    # The upgrade page is for moving onto a paid plan, so the free plan is never
    # offered here (unlike the renewal wall, which may continue a free grant).
    # Plans ticked "Hide on dashboard upgrade page" in the admin are left off too
    # (including the owner's own plan; their plan summary above still shows it).
    cards = [
        c for c in renewal.renewal_cards(sub.slug)
        if getattr(c, "plan_type", "") != "free"
        and not getattr(c, "hide_on_dashboard_upgrade", False)
    ]
    has_purchasable = any(getattr(c, "is_purchasable", False) for c in cards)

    # The checkout hands a paid purchase to Cashfree's hosted flow via their JS
    # SDK, so opt just this response into the payment-CSP relaxation (see
    # core.middleware)  and only when there is actually a paid plan to buy and
    # the gateway is configured. Otherwise the page stays nonce-only.
    cashfree_ready = cashfree.is_configured()
    if has_purchasable and cashfree_ready:
        request.csp_payment = True

    stores_used = request.user.sites.filter(is_deleted=False).count()

    return render(
        request,
        "ud/upgrade.html",
        {
            "nav_active": "upgrade",
            "subscription": sub,
            "cards": cards,
            "cashfree_ready": cashfree_ready,
            "stores_used": stores_used,
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def business_hub(request):
    """Business Hub: News / Help / Important posts OneWooz sends this owner.

    Posts are authored in the admin, either for every owner ("All users") or for
    chosen customers; each owner sees only what was sent to them. The tab is a
    plain ``?tab=`` link, so it works without JavaScript and can be bookmarked
    (the bell / email links open straight onto the right tab).

    The right-hand panel raises a **support ticket** (POST here, with an
    optional attachment). "Your tickets" opens a popup with every conversation
    and OneWooz's replies; ``?tickets=1`` (``&ticket=<id>`` for one thread)
    opens it on load, which is where ticket-reply notifications land. A reply
    from that popup (``action=reply``) is raised as a new follow-up ticket.
    """
    from django.contrib import messages
    from django.db import transaction
    from django.db.models import Prefetch

    from . import support
    from .forms import ATTACHMENT_ACCEPT, SupportReplyForm, SupportTicketForm
    from .models import BusinessHubPost, SupportTicket

    Kind = BusinessHubPost.Kind
    tab = (request.GET.get("tab") or "").strip().lower()
    if tab not in Kind.values:
        tab = Kind.NEWS

    def tickets_url(thread_id=None) -> str:
        url = f"{reverse('ud:business_hub')}?tab={tab}&tickets=1"
        return f"{url}&ticket={thread_id}" if thread_id else url

    ticket_form = SupportTicketForm(user=request.user)
    reply_form = None
    reply_thread_id = ""
    if request.method == "POST" and request.POST.get("action") == "reply":
        # A reply on one of the owner's own tickets → a new follow-up ticket in
        # the same conversation (always linked to the thread's first ticket).
        thread = None
        try:
            thread = (
                SupportTicket.objects.select_related("parent", "site")
                .filter(user=request.user, pk=uuid.UUID(str(request.POST.get("ticket_id") or "")))
                .first()
            )
        except (TypeError, ValueError):
            thread = None
        if thread is None:
            messages.error(request, "That ticket couldn't be found.")
            return redirect(tickets_url())
        root = thread.thread_root
        reply_thread_id = str(root.pk)
        reply_form = SupportReplyForm(request.POST, request.FILES)
        if reply_form.is_valid() and not ratelimit.consume(
            "support.ticket", str(request.user.pk)
        ).allowed:
            reply_form.add_error(
                None,
                "You've sent several tickets recently. Please wait a little before replying again.",
            )
        elif reply_form.is_valid():
            upload = reply_form.cleaned_data.get("attachment")
            subject = root.subject if root.subject.lower().startswith("re:") else f"Re: {root.subject}"
            follow_up = SupportTicket(
                user=request.user,
                parent=root,
                site=root.site,
                subject=subject[:160],
                category=root.category,
                priority=root.priority,
                message=reply_form.cleaned_data["message"],
            )
            if upload:
                follow_up.attachment = upload
                follow_up.attachment_name = (getattr(upload, "name", "") or "")[:255]
            follow_up.save()
            audit.record(
                "support.ticket.created", request=request, target=follow_up,
                target_kind="SupportTicket", target_label=follow_up.reference,
                site=follow_up.site,
            )
            follow_up_id = follow_up.pk
            transaction.on_commit(lambda: support.queue_team_email(follow_up_id))
            messages.success(
                request,
                f"Reply sent as ticket {follow_up.reference}. Our team will get back to you here.",
            )
            return redirect(tickets_url(root.pk))
    elif request.method == "POST":
        ticket_form = SupportTicketForm(request.POST, request.FILES, user=request.user)
        # Only a valid ticket counts towards the rate limit, so fixing a typo in
        # a rejected form never locks the owner out.
        if ticket_form.is_valid() and not ratelimit.consume(
            "support.ticket", str(request.user.pk)
        ).allowed:
            ticket_form.add_error(
                None,
                "You've raised several tickets recently. Please wait a little before raising another.",
            )
        elif ticket_form.is_valid():
            ticket = ticket_form.save(commit=False)
            ticket.user = request.user
            upload = ticket_form.cleaned_data.get("attachment")
            ticket.attachment_name = (getattr(upload, "name", "") or "")[:255] if upload else ""
            ticket.save()
            audit.record(
                "support.ticket.created", request=request, target=ticket,
                target_kind="SupportTicket", target_label=ticket.reference,
                site=ticket.site,
            )
            ticket_id = ticket.pk
            transaction.on_commit(lambda: support.queue_team_email(ticket_id))
            messages.success(
                request,
                f"Ticket {ticket.reference} raised. Our team will get back to you here on the Business Hub.",
            )
            return redirect(f"{reverse('ud:business_hub')}?tab={tab}#support")

    # Search by post title. The tab counts follow the search, so an owner can
    # see which tab holds the matches.
    q = " ".join((request.GET.get("q") or "").split())[:100]
    visible = BusinessHubPost.visible_to(request.user)
    if q:
        visible = visible.filter(title__icontains=q)
    tabs = []
    for value, label in Kind.choices:
        tabs.append({
            "key": value,
            "label": label,
            # Count keys only: the visibility join needs DISTINCT, and DISTINCT
            # over whole rows would carry every post body through MySQL.
            "count": visible.filter(kind=value).values_list("pk", flat=True).count(),
            "active": value == tab,
        })

    # Page over post ids, then load just this page's posts: DISTINCT + ORDER BY
    # over full rows (rich-text bodies included) can run MySQL out of sort
    # memory (error 1038). Same posts, same order, same Page object.
    page = paginate_sorted(
        visible.filter(kind=tab).order_by("-created_at"), 20, request.GET.get("page")
    )
    fresh_since = timezone.now() - timedelta(days=7)

    # Opening the hub reads its bell notifications: mark this owner's unread
    # Business Hub bell items for the open tab as read.
    try:
        from accounts.models import Notification

        from .hub import hub_url

        Notification.objects.filter(
            user=request.user, is_read=False, action_url=hub_url(tab)
        ).update(is_read=True, read_at=timezone.now())
    except Exception:
        pass

    # The owner's conversations: each first ticket with its follow-ups in order.
    open_statuses = [SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS]
    # ``select_related("site")`` joins each ticket to its store's whole
    # ``content`` JSON; sorting those joined rows can exceed MySQL's sort
    # buffer (error 1038), so only the ticket keys are sorted.
    tickets = sorted_rows(
        SupportTicket.objects.filter(user=request.user, parent__isnull=True)
        .select_related("site")
        .prefetch_related(
            Prefetch(
                "follow_ups",
                queryset=SupportTicket.objects.order_by("created_at"),
                to_attr="thread_replies",
            )
        ),
        limit=50,
    )
    for t in tickets:
        thread = [t, *t.thread_replies]
        t.thread_is_open = any(m.is_open for m in thread)
        t.thread_has_reply = any((m.reply or "").strip() for m in thread)
        t.thread_status = thread[-1].get_status_display() if t.thread_replies else t.get_status_display()
        t.thread_status_key = thread[-1].status
        t.last_activity = max(
            [m.created_at for m in thread] + [m.replied_at for m in thread if m.replied_at]
        )
    ticket_open_count = sum(1 for t in tickets if t.thread_is_open)

    return render(
        request,
        "ud/business_hub.html",
        {
            "nav_active": "business_hub",
            "tabs": tabs,
            "tab": tab,
            "tab_label": dict(Kind.choices)[tab],
            "q": q,
            "page_obj": page,
            "posts": list(page.object_list),
            "fresh_since": fresh_since,
            "ticket_form": ticket_form,
            "attachment_accept": ATTACHMENT_ACCEPT,
            "tickets": tickets,
            "ticket_open_count": ticket_open_count,
            "reply_form": reply_form,
            "reply_thread_id": reply_thread_id,
            # Open the "Your tickets" popup on load: a notification link, or a
            # reply that came back with errors.
            "open_tickets": bool(
                request.GET.get("tickets") or request.GET.get("ticket") or reply_form is not None
            ),
            "focus_ticket": reply_thread_id or (request.GET.get("ticket") or "").strip()[:36],
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def create_site(request):
    """Onboarding  captures the brand brief and creates the store record.

    On submit the store is created with a presentable seeded storefront (real
    copy, coherent palette, drafted policies), then the owner picks the design
    on the template-selection page before landing in the content editor.
    """
    from billing import subscriptions

    # The subscription caps how many stores an owner may build. Once they've used
    # them all, we don't ask them anything (business type, category, name)  we
    # just tell them to upgrade. This also refuses a direct POST, so the cap can't
    # be bypassed by skipping the form.
    used, store_limit = subscriptions.store_usage(request.user)
    if store_limit is not None and used >= store_limit:
        return render(
            request,
            "ud/create_site_locked.html",
            {"nav_active": "create", "store_limit": store_limit, "stores_used": used},
        )

    categories = list(
        BusinessCategory.objects.filter(is_active=True).order_by("order", "name")
    )
    currency_choices = Site.Currency.choices

    if request.method == "POST":
        business_type = (request.POST.get("business_type") or "d2c").strip()
        model_map = {
            "d2c": Site.BusinessModel.D2C,
            "b2b": Site.BusinessModel.B2B,
            "both": Site.BusinessModel.BOTH,
        }
        business_model = model_map.get(business_type, Site.BusinessModel.D2C)

        # A store must be classified: a category and one of its subcategories.
        category = (
            BusinessCategory.objects.filter(
                pk=request.POST.get("business_category") or "", is_active=True
            ).first()
        )
        subcategory = None
        if category is not None:
            subcategory = (
                BusinessSubCategory.objects.filter(
                    pk=request.POST.get("business_subcategory") or "",
                    category=category,
                    is_active=True,
                ).first()
            )

        errors = {}
        brand_name = (request.POST.get("brand_name") or "").strip()
        if not brand_name:
            errors["brand_name"] = "Enter a store name."
        if category is None:
            errors["business_category"] = "Choose a category."
        elif subcategory is None:
            errors["business_subcategory"] = "Choose a subcategory."

        currency = (request.POST.get("currency") or Site.Currency.INR).strip()
        if currency not in dict(Site.Currency.choices):
            currency = Site.Currency.INR

        if errors:
            return render(
                request,
                "ud/create_site.html",
                {
                    "nav_active": "create",
                    "submitted": False,
                    "categories": categories,
                    "currency_choices": currency_choices,
                    "errors": errors,
                    "form": request.POST,
                },
                status=400,
            )

        site = Site.objects.create(
            owner=request.user,
            name=brand_name[:120],
            tagline=(request.POST.get("tagline") or "").strip()[:200],
            business_model=business_model,
            theme=Site.default_theme_for(business_model),
            business_category=category,
            business_subcategory=subcategory,
            currency=currency,
            support_email=(request.POST.get("support_email") or "").strip()[:254],
            support_phone=(request.POST.get("support_phone") or "").strip()[:20],
        )
        # Seed presentable content now that the store has a name and slug.
        site.content = default_content(site)
        site.save(update_fields=["content", "updated_at"])
        return redirect("ud:select_template", site_id=site.id)

    return render(
        request,
        "ud/create_site.html",
        {
            "nav_active": "create",
            "submitted": False,
            "categories": categories,
            "currency_choices": currency_choices,
        },
    )


@login_required
def subcategories_json(request):
    """Active subcategories for a category  feeds the dependent dropdown on the
    create-store form.

    The category id arrives as a ``?category=<uuid>`` query parameter (not a path
    segment), so the front-end builds the URL by appending the chosen id rather
    than string-substituting a placeholder  nothing to get out of sync. An
    absent or malformed id simply yields an empty list.
    """
    raw = request.GET.get("category") or ""
    try:
        category_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return JsonResponse({"results": []})
    rows = (
        BusinessSubCategory.objects.filter(category_id=category_id, is_active=True)
        .order_by("order", "name")
        .values("id", "name")
    )
    return JsonResponse({"results": [{"id": str(r["id"]), "name": r["name"]} for r in rows]})


@login_required
def select_template(request, site_id):
    """Post-creation confirmation  the store is live and ready.

    The storefront design is assigned **automatically** from the store's
    business model at creation (D2C → the modern storefront ``template2``; B2B →
    the catalogue storefront ``template1``; see ``Site.default_theme_for``), so
    the owner is never asked to choose one. This screen just celebrates the
    launch and sends them into the editor, where the design remains switchable.
    """
    site = get_object_or_404(
        Site.objects.filter(is_deleted=False), pk=site_id, owner=request.user
    )
    return render(
        request,
        "ud/select_template.html",
        {
            "nav_active": "create",
            "site": site,
            "storefront_url": site.storefront_url(request),
            "editor_url": reverse("store:editor", args=[site.id]),
        },
    )


# --------------------------------------------------------------------------
# Deleting a store  confirmation + emailed one-time code
# --------------------------------------------------------------------------
#
# Deleting a store is destructive and hard to undo from the owner's side, so it
# is gated the same way a sign-in is: the owner confirms, we email a one-time
# code (issued and verified server-side by ``accounts.otp``), and only a correct
# code performs the soft-delete. The two steps are separate endpoints so the UI
# can confirm, then collect the code, without ever holding it client-side.


def _owned_live_site(request, site_id):
    """The requester's own, not-yet-deleted store, or a 404."""
    return get_object_or_404(
        Site.objects.filter(is_deleted=False), pk=site_id, owner=request.user
    )


def _mask_email(email: str) -> str:
    """``jane.doe@example.com`` → ``j•••@example.com`` for display in the UI."""
    email = (email or "").strip()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    shown = local[0] if local else ""
    return f"{shown}•••@{domain}"


def _delete_purpose(site) -> str:
    # Purpose is salted with the store id, so a code minted to delete one store
    # can never be replayed to delete another.
    return f"site_delete:{site.id}"


@require_POST
@login_required
def delete_site_send_code(request, site_id):
    """Step 1  email the owner a one-time code to confirm deletion."""
    site = _owned_live_site(request, site_id)
    email = (request.user.email or "").strip()
    if not email:
        return JsonResponse(
            {"ok": False, "error": "Your account has no email to send a code to."},
            status=400,
        )

    ip = ratelimit.client_identity(request)
    if not ratelimit.consume("otp.send.email", email).allowed or not ratelimit.consume(
        "otp.send.ip", ip
    ).allowed:
        return JsonResponse(
            {"ok": False, "error": "Too many requests. Please wait a minute and try again."},
            status=429,
        )

    code = otp.issue(_delete_purpose(site), email)
    if not emails.send_site_delete_code(
        email, code, site_name=site.name, minutes=otp.TTL_SECONDS // 60
    ):
        return JsonResponse(
            {"ok": False, "error": "We couldn't send the code right now. Please try again."},
            status=502,
        )
    return JsonResponse(
        {"ok": True, "email": _mask_email(email), "expires_in": otp.TTL_SECONDS}
    )


@require_POST
@login_required
def delete_site_confirm(request, site_id):
    """Step 2  verify the emailed code and soft-delete the store."""
    site = _owned_live_site(request, site_id)
    email = (request.user.email or "").strip()

    if not ratelimit.consume("otp.verify", email).allowed:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait and try again."},
            status=429,
        )

    code = (request.POST.get("code") or "").strip()
    if not code:
        return JsonResponse(
            {"ok": False, "error": "Enter the code we emailed you."}, status=400
        )

    result = otp.verify(_delete_purpose(site), email, code)
    if not result.ok:
        message = {
            "expired": "That code has expired. Send a new one.",
            "too_many_attempts": "Too many incorrect attempts. Send a new code.",
            "incorrect": "That code isn't right. Check the email and try again.",
        }.get(result.reason, "That code isn't right. Please try again.")
        return JsonResponse({"ok": False, "error": message}, status=400)

    ratelimit.reset("otp.verify", email)

    site_label = site.name
    site.soft_delete()
    audit.record(
        "store.deleted",
        request=request,
        actor=request.user,
        target=site,
        target_kind="Site",
        target_label=site_label,
        site=site,
        severity="notice",
    )

    # If the deleted store was the active profile, forget it so the dashboard
    # and switcher don't try to resume a store that's gone.
    sid = str(site.id)
    if str(getattr(request.user, "active_site_id", "") or "") == sid:
        try:
            request.user.__class__.objects.filter(pk=request.user.pk).update(
                active_site_id=None
            )
            request.user.active_site_id = None
        except Exception:
            pass
    if request.session.get("active_site_id") == sid:
        try:
            del request.session["active_site_id"]
        except KeyError:
            pass

    return JsonResponse({"ok": True, "redirect": reverse("ud:dashboard")})
