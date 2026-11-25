"""Editorial layer for marketplace.onewooz.com: problems, stacks, visuals.

The catalogue (:mod:`marketplace.catalog`) says *what* each app is. This module
says *why an owner would want it*: every category maps to the store problem it
solves, apps that work well together are grouped into "power stacks", and the
placement data is turned into the zones the app page marks on its
dashboard and storefront diagrams.

Pure functions over the cached catalogue dicts: no queries, no state. Copy
rules: never say "free", no em dashes, no traction numbers.
"""

from __future__ import annotations

from django.urls import reverse

#: category -> the problem it solves. Order here is the order on the page.
PROBLEMS: dict[str, dict] = {
    "sales": {
        "problem": "Visitors browse, then leave without buying.",
        "short": "Shoppers leave without buying",
        "fix": "Give them a reason to act now: urgency, offers and product pages that close the sale.",
        "icon": "tag",
    },
    "marketing": {
        "problem": "Nobody hears about your offers.",
        "short": "Nobody sees my offers",
        "fix": "Put your sale, launch or shipping offer in front of every single visitor.",
        "icon": "bullhorn",
    },
    "support": {
        "problem": "Shoppers can't reach you when they have a question.",
        "short": "Shoppers can't reach me",
        "fix": "Answer in one tap, on the channels your shoppers already use every day.",
        "icon": "inbox",
    },
    "trust": {
        "problem": "First-time shoppers don't trust a new store.",
        "short": "New shoppers don't trust me",
        "fix": "Show reviews, badges and proof right where shoppers decide to buy.",
        "icon": "star",
    },
    "design": {
        "problem": "Your store looks like everyone else's.",
        "short": "My store looks generic",
        "fix": "Switch to a new storefront design without redoing a single product or page.",
        "icon": "layout",
    },
    "analytics": {
        "problem": "You can't see what's actually working.",
        "short": "I can't see what works",
        "fix": "Track visits and campaigns with the tools you already know.",
        "icon": "chart",
    },
    "operations": {
        "problem": "Busywork eats the hours you should spend selling.",
        "short": "Busywork eats my day",
        "fix": "Hand the repetitive parts of orders and stock to a plugin.",
        "icon": "box",
    },
    "other": {
        "problem": "Your store needs something a little different.",
        "short": "I need something specific",
        "fix": "Handy extras for the jobs in between.",
        "icon": "grid",
    },
}

#: Apps that work better together. A stack shows when at least two of its
#: categories have a published app.
STACKS: tuple[dict, ...] = (
    {
        "key": "launch",
        "name": "Launch day",
        "pitch": "Announce it, add urgency, and be there when shoppers have questions.",
        "cats": ("marketing", "sales", "support"),
    },
    {
        "key": "trust",
        "name": "First-sale confidence",
        "pitch": "Answer doubts before they turn into closed tabs.",
        "cats": ("trust", "support", "design"),
    },
    {
        "key": "growth",
        "name": "Growth engine",
        "pitch": "See what works, then put more of it in front of every visitor.",
        "cats": ("analytics", "marketing", "sales"),
    },
    {
        "key": "glowup",
        "name": "Fresh look",
        "pitch": "A new storefront design plus a message that tells shoppers what's new.",
        "cats": ("design", "marketing"),
    },
)

#: dashboard placement point -> zone on the dashboard diagram
_POINT_ZONE = {
    "sidebar": "sidebar",
    "editor_link": "sidebar",
    "top_bar": "topbar",
    "settings_card": "card",
    "overview_card": "card",
    "dashboard_card": "card",
    "product_panel": "panel",
    "order_panel": "panel",
}


def _slot_zone(slot: str) -> str:
    if slot in ("header.above", "body.start"):
        return "top"
    if slot in ("header.below", "home.after_hero", "collection.top"):
        return "below"
    if slot in ("footer.above", "body.end"):
        return "footer"
    if slot == "float.bottom_right":
        return "float_r"
    if slot == "float.bottom_left":
        return "float_l"
    if slot == "popup":
        return "popup"
    if slot == "head.end":
        return "head"
    return "inline"


def problem_for(category: str) -> dict:
    p = PROBLEMS.get(category) or PROBLEMS["other"]
    return {"key": category, **p}


def problems(rows: list[dict]) -> list[dict]:
    """Problems that have at least one published app, with their apps."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    out = []
    for key in PROBLEMS:
        apps = by_cat.get(key)
        if not apps:
            continue
        out.append({**problem_for(key), "label": apps[0]["category_label"], "apps": apps[:4], "n": len(apps)})
    return out


def stacks(rows: list[dict]) -> list[dict]:
    """Power stacks: one app per category, shown when two or more resolve."""
    first: dict[str, dict] = {}
    for r in rows:
        first.setdefault(r["category"], r)
    out = []
    for s in STACKS:
        apps = [first[c] for c in s["cats"] if c in first]
        if len(apps) < 2:
            continue
        out.append({**s, "apps": apps})
    return out


def related(rows: list[dict], app: dict, limit: int = 3) -> list[dict]:
    """Same category first, then apps from the stacks this app belongs to."""
    same = [r for r in rows if r["slug"] != app["slug"] and r["category"] == app["category"]]
    partner_cats: list[str] = []
    for s in STACKS:
        if app["category"] in s["cats"]:
            partner_cats += [c for c in s["cats"] if c != app["category"] and c not in partner_cats]
    partners = [r for c in partner_cats for r in rows if r["category"] == c and r["slug"] != app["slug"]]
    rest = [r for r in rows if r["slug"] != app["slug"]]
    seen: set[str] = set()
    out = []
    for r in same + partners + rest:
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _browse(**params) -> str:
    from urllib.parse import urlencode

    clean = {k: v for k, v in params.items() if v}
    return reverse("marketplace:browse") + ("?" + urlencode(clean) if clean else "")


def category_tiles(rows: list[dict]) -> list[dict]:
    """"Browse by category" tiles, in problem order, with counts."""
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
        labels.setdefault(r["category"], r["category_label"])
    out = []
    for key in PROBLEMS:
        if key in counts:
            p = problem_for(key)
            out.append({"key": key, "label": labels[key], "n": counts[key], "icon": p["icon"],
                        "need": p["short"], "url": _browse(c=key)})
    return out


def collections(rows: list[dict], size: int = 4) -> list[dict]:
    """Curated rows for the home page (Staff picks, Recently added, ...).

    Staff picks lead with featured apps and fill up from the rest. Other rows
    only show with at least two apps; "See all" goes to the browse page
    with the matching filter or sort.
    """
    featured = [r for r in rows if r["featured"]]
    picks = featured + [r for r in rows if not r["featured"]]
    newest = sorted(rows, key=lambda r: r.get("published") or "", reverse=True)
    included = [r for r in rows if not r["is_paid"]]
    premium = [r for r in rows if r["is_paid"]]
    spec = (
        ("picks", "Staff picks", "Chosen by the OneWooz team for new stores.", picks, _browse()),
        ("new", "Recently added", "The newest apps in the catalogue.", newest, _browse(sort="newest")),
        ("included", "Included with your store", "Add these at no extra charge.", included, _browse(price="included")),
        ("premium", "Premium apps", "Paid apps, most with a trial first.", premium, _browse(price="paid")),
    )
    out = []
    for key, title, sub, apps, url in spec:
        if len(apps) >= 2:
            out.append({"key": key, "title": title, "sub": sub, "apps": apps[:size], "url": url, "n": len(apps)})
    return out


def plug_zones(app: dict) -> dict:
    """Zones lit on the app page's dashboard + storefront diagrams."""
    dash = {_POINT_ZONE.get(d.get("key", ""), "card") for d in app.get("dashboard", [])}
    store = {_slot_zone(s.get("slot", "")) for s in app.get("storefront", [])}
    return {"dash": sorted(dash), "store": sorted(store)}


def palette(rows: list[dict]) -> list[dict]:
    """Command palette entries (serialised with json_script)."""
    out = []
    for r in rows:
        out.append(
            {
                "n": r["name"],
                "t": r["tagline"] or "",
                "c": r["category_label"],
                "u": reverse("marketplace:app", kwargs={"slug": r["slug"]}),
                "a": r.get("accent") or "#4b2bf5",
                "k": "app",
            }
        )
    seen = set()
    for r in rows:
        key = r["category"]
        if key in seen:
            continue
        seen.add(key)
        p = problem_for(key)
        out.append({"n": p["short"], "t": p["fix"], "c": "Problem", "u": _browse(c=key),
                    "a": "", "k": "problem"})
    return out
