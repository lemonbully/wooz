"""marketplace.onewooz.com: public browsing + the owner's library (read only).

Nobody needs to sign in to see apps and designs. Installing or buying sends the
visitor to ``app.onewooz.com`` (the dashboard) where they re-authenticate first
(:mod:`marketplace.gateway`). This surface never installs, charges or touches a
store itself; a compromise here cannot reach store data.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST


from core.links import surface_url

from . import auth, catalog, links, showcase


def _closed(request):
    from .models import MarketplaceSettings

    cfg = MarketplaceSettings.current()
    if cfg.is_enabled:
        return None
    return render(request, "marketplace/surface/closed.html", _ctx(request, message=cfg.closed_message), status=503)


def _owner_state(request) -> dict:
    """Signed-in owner's installed apps and design library (for chips)."""
    owner = auth.current_owner(request)
    state = {"owner": owner, "installed": set(), "items": {}, "apps": set()}
    if not owner:
        return state
    try:
        from .licences import Owner, active_licences, installed_apps
        from uuid import UUID

        o = Owner(ref=UUID(owner["ref"]), email=owner["email"])
        state["installed"] = {a.slug for a in installed_apps(o)}
        for lic in active_licences(o):
            if lic.item_key:
                state["items"].setdefault(lic.app.slug, {})[lic.item_key] = lic
            else:
                state["apps"].add(lic.app.slug)
    except Exception:
        pass
    return state


def _shell(request) -> dict:
    """Command palette + footer categories (same cached catalogue on every page)."""
    try:
        rows = catalog.listing()
        return {
            "palette": showcase.palette(rows),
            "nav_categories": [
                {"key": k, "label": label}
                for k, label in catalog.categories(rows)
            ],
            "app_total": len(rows),
        }
    except Exception:
        return {"palette": [], "nav_categories": [], "app_total": 0}


def _ctx(request, **extra):
    from .models import MarketplaceSettings

    ctx = {
        **_shell(request),
        "mkt_owner": auth.current_owner(request),
        "mkt_settings": MarketplaceSettings.current(),
        "dashboard_url": links.app_url(request, "ud:dashboard"),
        "connect_url": reverse("marketplace:connect") + "?" + urlencode({"next": request.get_full_path()}),
        "signup_url": surface_url("app", "/sign-up/", request),
        "onewooz_home": surface_url("marketing", "/", request),
        "security_url": surface_url("marketing", "/security/", request),
        "support_url": surface_url("marketing", "/support/", request),
    }
    ctx.update(extra)
    return ctx


SORTS = (
    ("featured", "Featured"),
    ("newest", "Newest"),
    ("name", "Name, A to Z"),
    ("price_low", "Price, low to high"),
    ("price_high", "Price, high to low"),
)
PRICES = (("included", "Included"), ("paid", "Premium"), ("trial", "Has a trial"))
FITS = (("d2c", "D2C stores"), ("b2b", "B2B stores"))


def _designs(request, limit: int = 3) -> tuple[list[dict], int]:
    designs = catalog.detail("templates") or {}
    items = designs.get("items", [])
    rows = [
        {**it, "url": reverse("marketplace:item", kwargs={"slug": "templates", "key": it["key"]})}
        for it in items[:limit]
    ]
    return rows, len(items)


@require_GET
def home(request):
    """The storefront of the marketplace: search, categories, curated rows."""
    closed = _closed(request)
    if closed:
        return closed
    if request.GET.get("q") or request.GET.get("c"):
        # Old catalogue links (?q= / ?c=) now live on the browse page.
        params = {k: v for k, v in (("q", request.GET.get("q")), ("c", request.GET.get("c"))) if v}
        return redirect(reverse("marketplace:browse") + "?" + urlencode(params))
    rows = catalog.listing()
    state = _owner_state(request)
    design_items, design_total = _designs(request)
    featured = [r for r in rows if r["featured"]]
    return render(
        request,
        "marketplace/surface/home.html",
        _ctx(
            request,
            spotlight=(featured or rows or [None])[0],
            tiles=showcase.category_tiles(rows),
            collections=showcase.collections(rows),
            problems=showcase.problems(rows),
            stacks=showcase.stacks(rows),
            design_items=design_items,
            stats={"apps": len(rows), "designs": design_total, "categories": len({r["category"] for r in rows})},
            installed=state["installed"],
            nav="home",
            page_title="OneWooz Apps",
        ),
    )


@require_GET
def browse(request):
    """Search results and category pages: filter sidebar + sorted results."""
    closed = _closed(request)
    if closed:
        return closed
    rows = catalog.listing()
    q = (request.GET.get("q") or "").strip()[:80]
    cat = (request.GET.get("c") or "").strip()[:20]
    price = (request.GET.get("price") or "").strip()[:10]
    fit = (request.GET.get("fit") or "").strip()[:4]
    sort = (request.GET.get("sort") or "featured").strip()[:12]
    if price not in dict(PRICES):
        price = ""
    if fit not in dict(FITS):
        fit = ""
    if sort not in dict(SORTS):
        sort = "featured"

    def match_q(r):
        if not q:
            return True
        hay = " ".join((r["name"], r["tagline"] or "", r["category_label"], showcase.problem_for(r["category"])["short"])).lower()
        return all(w in hay for w in q.lower().split())

    def match_price(r, want=None):
        want = price if want is None else want
        if want == "included":
            return not r["is_paid"]
        if want == "paid":
            return r["is_paid"]
        if want == "trial":
            return bool(r["is_paid"] and r["trial_days"])
        return True

    def match_fit(r):
        return not fit or r["fits"] in ("both", fit)

    base = [r for r in rows if match_q(r)]
    shown = [r for r in base if (not cat or r["category"] == cat) and match_price(r) and match_fit(r)]

    if sort == "newest":
        shown.sort(key=lambda r: r.get("published") or "", reverse=True)
    elif sort == "name":
        shown.sort(key=lambda r: r["name"].lower())
    elif sort == "price_low":
        shown.sort(key=lambda r: r.get("price_num") or 0)
    elif sort == "price_high":
        shown.sort(key=lambda r: r.get("price_num") or 0, reverse=True)
    else:
        shown.sort(key=lambda r: (not r["featured"],))

    def url(**over):
        params = {"q": q, "c": cat, "price": price, "fit": fit, "sort": sort if sort != "featured" else ""}
        params.update(over)
        clean = {k: v for k, v in params.items() if v}
        return reverse("marketplace:browse") + ("?" + urlencode(clean) if clean else "")

    # Facet counts respect every other active filter (Amazon-style).
    cat_base = [r for r in base if match_price(r) and match_fit(r)]
    counts: dict[str, int] = {}
    for r in cat_base:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    categories = [
        {"key": k, "label": label, "n": counts.get(k, 0), "url": url(c=k), "on": cat == k}
        for k, label in catalog.categories(rows)
    ]
    price_base = [r for r in base if (not cat or r["category"] == cat) and match_fit(r)]
    prices = [
        {"key": k, "label": label, "n": sum(1 for r in price_base if match_price(r, k)),
         "url": url(price="" if price == k else k), "on": price == k}
        for k, label in PRICES
    ]
    fit_base = [r for r in base if (not cat or r["category"] == cat) and match_price(r)]
    fits = [
        {"key": k, "label": label, "n": sum(1 for r in fit_base if r["fits"] in ("both", k)),
         "url": url(fit="" if fit == k else k), "on": fit == k}
        for k, label in FITS
    ]
    cat_label = next((c["label"] for c in categories if c["key"] == cat), "")
    active = []
    if q:
        active.append({"label": f"“{q}”", "url": url(q="")})
    if cat_label:
        active.append({"label": cat_label, "url": url(c="")})
    if price:
        active.append({"label": dict(PRICES)[price], "url": url(price="")})
    if fit:
        active.append({"label": dict(FITS)[fit], "url": url(fit="")})
    state = _owner_state(request)
    title = f"Results for “{q}”" if q else (cat_label or "All apps")
    return render(
        request,
        "marketplace/surface/browse.html",
        _ctx(
            request,
            apps=shown,
            total=len(rows),
            q=q,
            cat=cat,
            cat_label=cat_label,
            cat_problem=showcase.problem_for(cat) if cat_label else None,
            price=price,
            fit=fit,
            sort=sort,
            sorts=[{"key": k, "label": label} for k, label in SORTS],
            categories=categories,
            prices=prices,
            fits=fits,
            active=active,
            clear_url=reverse("marketplace:browse"),
            heading=title,
            suggestions=showcase.collections(rows)[0]["apps"] if not shown and rows else [],
            installed=state["installed"],
            nav="browse",
            page_title=title,
        ),
    )


def _install_url(request, slug: str, key: str = "", mode: str = "") -> str:
    params = {"app": slug}
    if key:
        params["item"] = key
    if mode:
        params["mode"] = mode
    return links.app_url(request, "mkt_gateway:authorize", urlencode(params))


@require_GET
def app_detail(request, slug):
    closed = _closed(request)
    if closed:
        return closed
    app = catalog.detail(slug)
    if app is None:
        raise Http404("No such app.")
    state = _owner_state(request)
    all_items = _item_rows(request, app, state)
    installed = slug in state["installed"]
    common = dict(
        app=app,
        installed=installed,
        install_url=_install_url(request, slug),
        trial_url=_install_url(request, slug, mode="trial") if app["trial_days"] and app["is_paid"] else "",
        page_title=app["name"],
        page_description=app["tagline"],
    )
    if app.get("is_designs"):
        fit = (request.GET.get("fit") or "").strip()
        price = (request.GET.get("price") or "").strip()
        items = all_items
        if fit in ("d2c", "b2b"):
            items = [i for i in items if i["fits"] in ("both", fit)]
        else:
            fit = ""
        if price == "included":
            items = [i for i in items if not i["is_priced"]]
        elif price == "paid":
            items = [i for i in items if i["is_priced"]]
        else:
            price = ""
        hero = next((i for i in all_items if not i["is_system_default"]), all_items[0] if all_items else None)
        return render(
            request,
            "marketplace/surface/app_templates.html",
            _ctx(request, items=items, fit=fit, price=price, design_total=len(all_items), hero_item=hero,
                 nav="designs", **common),
        )
    adds = []
    for d in app.get("dashboard", []):
        adds.append({"label": d["label"], "desc": f"{d['point']}.", "tag": "Dashboard"})
    for sf in app.get("storefront", []):
        adds.append({"label": sf["label"], "desc": f"{sf['widget']} on your storefront. Stays off until you switch it on.",
                     "tag": "Storefront"})
    for i, row in enumerate(adds, start=1):
        row["num"] = f"{i:02d}"
    try:
        related = showcase.related(catalog.listing(), app)
    except Exception:
        related = []
    return render(
        request,
        "marketplace/surface/app_detail.html",
        _ctx(
            request,
            items=all_items,
            adds=adds,
            related=related,
            problem=showcase.problem_for(app["category"]),
            zones=showcase.plug_zones(app),
            installed_set=state["installed"],
            launched=_launched(app),
            nav="home",
            **common,
        ),
    )


def _launched(app: dict):
    from datetime import date

    try:
        return date.fromisoformat(app.get("published") or "")
    except ValueError:
        return None


def _item_rows(request, app: dict, state: dict) -> list[dict]:
    owned_items = state["items"].get(app["slug"], {})
    rows = []
    for it in app.get("items", []):
        lic = owned_items.get(it["key"])
        rows.append(
            {
                **it,
                "owned": bool(lic) or it["is_system_default"],
                "expires_at": getattr(lic, "expires_at", None),
                "install_url": _install_url(request, app["slug"], it["key"]),
                "url": reverse("marketplace:item", kwargs={"slug": app["slug"], "key": it["key"]}),
            }
        )
    return rows


@require_GET
def item_detail(request, slug, key):
    closed = _closed(request)
    if closed:
        return closed
    app = catalog.detail(slug)
    if app is None:
        raise Http404("No such app.")
    item = next((i for i in app.get("items", []) if i["key"] == key), None)
    if item is None:
        raise Http404("No such item.")
    state = _owner_state(request)
    lic = state["items"].get(slug, {}).get(key)
    others = [i for i in _item_rows(request, app, state) if i["key"] != key][:3]
    return render(
        request,
        "marketplace/surface/item_detail.html",
        _ctx(
            request,
            app=app,
            item=item,
            owned=bool(lic) or item["is_system_default"],
            licence=lic,
            others=others,
            install_url=_install_url(request, slug, key),
            nav="designs" if app.get("is_designs") else "home",
            page_title=f"{item['name']} \u00b7 {app['name']}",
            page_description=item.get("tagline") or app["tagline"],
        ),
    )


@require_GET
def install(request, slug, key=""):
    """Install / Add / Buy: always continues on the dashboard, after re-auth."""
    closed = _closed(request)
    if closed:
        return closed
    return HttpResponseRedirect(_install_url(request, slug, key, (request.GET.get("mode") or "")[:10]))


@require_GET
def connect(request):
    """Sign in: the dashboard authenticates, then hands the visitor back."""
    nxt = _safe_next(request, request.GET.get("next") or "")
    return HttpResponseRedirect(links.app_url(request, "mkt_gateway:connect", urlencode({"next": nxt})))


@require_GET
def handoff(request):
    nxt = _safe_next(request, request.GET.get("next") or "")
    try:
        auth.redeem_handoff(request, request.GET.get("t") or "")
    except auth.HandoffError:
        return render(request, "marketplace/surface/handoff_failed.html", _ctx(request), status=400)
    return HttpResponseRedirect(nxt or reverse("marketplace:home"))


@require_POST
def sign_out(request):
    auth.sign_out(request)
    return redirect("marketplace:home")


@require_GET
def library(request):
    owner = auth.current_owner(request)
    if not owner:
        return redirect(reverse("marketplace:connect") + "?" + urlencode({"next": request.get_full_path()}))
    from uuid import UUID

    from .licences import Owner, active_licences, installed_apps

    o = Owner(ref=UUID(owner["ref"]), email=owner["email"])
    try:
        apps = [catalog.app_card(a) for a in installed_apps(o)]
        lics = active_licences(o)
    except Exception:
        apps, lics = [], []
    return render(
        request,
        "marketplace/surface/library.html",
        _ctx(request, apps=apps, licences=lics, my_apps_url=links.app_url(request, "mkt_gateway:my_apps"),
             nav="library", page_title="My apps"),
    )


def _safe_next(request, value: str) -> str:
    """Only a path on this marketplace host (no open redirect)."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return ""
    if not url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}):
        return ""
    return value[:500]
