"""A store's extra (owner-named) policy pages.

Besides the four built-in policies (privacy, terms, refund, shipping), the Edit
Content **Policies** tab offers three optional custom policies. Each is stored in
``Site.content`` as ``custom_policy_<n>`` = ``{"title": ..., "content": ...}``
(n = 1..3, see ``commerce.views.editor``).

A slot is *published* once it has a title or a body. Each published policy is:

* served at ``/policies/<slug>/`` on the store (``public_policy_page``) and on a
  collaborator's UGC storefront (same route name, referral urlconf);
* linked from the store footer (the "Legal" column), the UGC storefront footer
  and the policy tabs at the top of every policy page;
* listed in the store sitemap and the editor's per-page SEO tab
  (page key ``policy:<n>``, stable across title changes).

The slug comes from the title (``slugify``), falling back to ``policy-<n>``. It
never collides with another custom policy's slug.
"""

from __future__ import annotations

from django.utils.text import slugify

#: How many custom policy slots the editor offers.
SLOTS = (1, 2, 3)

#: Content key of slot ``n``.
def content_key(n: int) -> str:
    return f"custom_policy_{n}"


def _get(content, key):
    try:
        return content.get(key) if content is not None else None
    except Exception:
        return None


def _plain(value) -> str:
    return " ".join(str(value or "").split())


def custom_policies(content) -> list[dict]:
    """Every published custom policy, in slot order:
    ``[{"n", "key", "title", "slug", "content"}]``. ``content`` is the site's
    content dict (or the storefront's wrapped content); anything unreadable is
    skipped, never raised."""
    out: list[dict] = []
    used: set[str] = set()
    for n in SLOTS:
        raw = _get(content, content_key(n))
        # Skip an unset slot (None, {} or the storefront's falsy MISSING object).
        if not raw or not hasattr(raw, "get"):
            continue
        try:
            title = _plain(raw.get("title") or "")[:120]
            body = str(raw.get("content") or "").strip()
        except Exception:
            continue
        if not title and not body:
            continue
        title = title or f"Policy {n}"
        slug = (slugify(title) or f"policy-{n}")[:80].strip("-") or f"policy-{n}"
        if slug in used:
            slug = f"{slug}-{n}"
        used.add(slug)
        out.append({"n": n, "key": f"policy:{n}", "title": title, "slug": slug, "content": body})
    return out


def find(content, slug: str) -> dict | None:
    """The published custom policy at ``slug``, or ``None``."""
    slug = str(slug or "").strip().lower()
    return next((p for p in custom_policies(content) if p["slug"] == slug), None)


def by_number(content, n) -> dict | None:
    """The published custom policy in slot ``n``, or ``None``."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return next((p for p in custom_policies(content) if p["n"] == n), None)
