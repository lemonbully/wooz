"""Whether a subdomain label may be used by a store.

A store's ``slug`` is its public subdomain under ``.onewooz.site``, and a UGC
collaboration picks its own subdomain too. Both are checked here against:

* a **built-in set** of labels the platform reserves structurally (its own
  hostnames, mail/infra labels, and the surface names), which can never be
  freed from the admin; plus
* the operator-managed :class:`tenancy.models.ReservedSubdomain` table.

The check is case-insensitive and never raises: if the table can't be read (for
example before its migration has run) only the built-in set applies, so store
creation is never blocked by an infrastructure hiccup.
"""

from __future__ import annotations

# Labels the platform reserves no matter what. Kept deliberately broad: these
# are cheap to forbid and expensive to have collide with a real host or route.
BUILTIN_RESERVED: frozenset[str] = frozenset(
    {
        # Platform surfaces / hosts.
        "www", "app", "apps", "admin", "administrator", "dashboard",
        "onewooz", "web", "store", "storefront", "shop", "portal", "my",
        # Infrastructure / mail.
        "api", "cdn", "static", "assets", "media", "img", "images", "files",
        "mail", "email", "smtp", "imap", "pop", "pop3", "webmail", "mx",
        "ns", "ns1", "ns2", "dns", "ftp", "sftp", "ssh", "vpn", "proxy",
        "no-reply", "noreply", "bounce", "postmaster", "hostmaster",
        # Product / billing / auth surfaces.
        "billing", "pay", "payment", "payments", "checkout", "cart", "order",
        "orders", "account", "accounts", "login", "signin", "sign-in",
        "signup", "sign-up", "register", "auth", "sso", "oauth",
        "security", "support", "help", "helpdesk", "status", "docs", "blog",
        "about", "contact", "pricing", "demo", "example", "test", "testing",
        "dev", "development", "stage", "staging", "preview", "sandbox",
        "beta", "alpha", "internal", "system", "root", "null", "undefined",
    }
)


def normalise(label: str) -> str:
    return (label or "").strip().lower()


def is_reserved(label: str) -> bool:
    """True when ``label`` may not be used as a store/collaboration subdomain."""
    label = normalise(label)
    if not label:
        return False
    if label in BUILTIN_RESERVED:
        return True
    try:
        from .models import ReservedSubdomain

        return ReservedSubdomain.objects.filter(keyword=label, is_active=True).exists()
    except Exception:
        # Table not ready / DB error: fall back to the built-in set only, so a
        # store can still be created rather than 500ing.
        return False
