"""Staff/team access control for the store dashboard.

An owner can grant other people scoped access to their store (see
:class:`commerce.models.StaffMember`). This module is the brain of that:

* :func:`assignable_pages`  the catalogue of pages an owner may hand out
  (every store sidebar area except Settings), with the four actions.
* :func:`access_for`  resolve a request+site to a :class:`StaffAccess`
  (owner → full; staff → their grant; nobody → ``None``), linking a staff
  member to their account by email on first open.
* :func:`required_action`  the permission a request needs (view for safe
  reads; create / edit / delete for writes), so the middleware can enforce
  action-level permissions.

The owner is always all-powerful on their own store; only *staff* are gated. The
gate itself lives in :class:`commerce.middleware.StaffAccessGateMiddleware`, and
the nav is filtered in :func:`commerce.context.visible_groups`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The four actions a page grant can carry, in display order. "view" is the
# baseline  a page is only accessible if it grants at least "view".
ACTIONS: list[tuple[str, str]] = [
    ("view", "View"),
    ("create", "Create"),
    ("edit", "Edit"),
    ("delete", "Delete"),
]
ACTION_KEYS = [a for a, _ in ACTIONS]

# Sidebar area that is never handed to staff (owner-only account/store settings).
_NON_ASSIGNABLE = {"settings"}


def assignable_pages() -> list[dict]:
    """The pages an owner can grant to staff  every store area but Settings.

    Returns ``[{"key", "label", "group"}]`` in sidebar order, so the management
    UI groups the checkboxes exactly like the dashboard sidebar.
    """
    from .context import NAV_GROUPS  # local import avoids an import cycle

    out: list[dict] = []
    for group in NAV_GROUPS:
        for item in group.items:
            if item.key in _NON_ASSIGNABLE:
                continue
            out.append({"key": item.key, "label": item.label, "group": group.label})
    return out


def assignable_page_keys() -> set[str]:
    return {p["key"] for p in assignable_pages()}


# --------------------------------------------------------------------------
# Route → action classification
# --------------------------------------------------------------------------
# The action a request needs is derived from the HTTP method first, then the
# route name. A safe (GET/HEAD) request only ever needs "view"  opening a list,
# a detail page or even an edit form reads, it does not change anything. An
# unsafe request (POST/…) needs the specific action: "create" or "delete" when
# the route name says so, otherwise "edit" (the safe default  any other
# mutation demands at least edit rights, so a view-only member can never write).
_CREATE_RE = re.compile(r"(_new|_add|_create|_invite|_generate)")
_DELETE_RE = re.compile(r"(_delete|_remove|_destroy|_terminate)")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Explicit overrides where the route name would misclassify a mutation.
_ACTION_OVERRIDES: dict[str, str] = {
    "bill_generate": "create",
}


def required_action(url_name: str | None, method: str) -> str:
    """The permission a request needs: view / create / edit / delete."""
    if (method or "GET").upper() in _SAFE_METHODS:
        return "view"
    n = url_name or ""
    if n in _ACTION_OVERRIDES:
        return _ACTION_OVERRIDES[n]
    if _DELETE_RE.search(n):
        return "delete"
    if _CREATE_RE.search(n):
        return "create"
    return "edit"


def page_for_urlname(url_name: str | None) -> str | None:
    """The sidebar page a store route belongs to (reuses the feature map)."""
    from .features import feature_for_urlname

    return feature_for_urlname(url_name)


# --------------------------------------------------------------------------
# Access resolution
# --------------------------------------------------------------------------
@dataclass
class StaffAccess:
    """What a signed-in user may do on a given store.

    ``is_owner`` → everything (``permissions`` is ``None``). Otherwise
    ``permissions`` is the staff grant ``{page_key: [actions]}``.
    """

    is_owner: bool
    permissions: dict | None = None

    def allowed_pages(self) -> set[str] | None:
        """Page keys the user may open  ``None`` means all (owner)."""
        if self.is_owner:
            return None
        perms = self.permissions if isinstance(self.permissions, dict) else {}
        return {k for k, v in perms.items() if v}

    def can(self, page: str | None, action: str) -> bool:
        if self.is_owner:
            return True
        if not page:
            return False
        acts = (self.permissions or {}).get(page) or []
        return action in acts

    def can_view(self, page: str | None) -> bool:
        return self.can(page, "view")


def access_for(request, site) -> StaffAccess | None:
    """Resolve the signed-in user's access to ``site``.

    Owner → full access. Otherwise an active, non-deleted :class:`StaffMember`
    matched by linked account or (once) by email  linking the account on first
    open. Returns ``None`` when the user has no access at all (the caller 404s).
    """
    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    if getattr(site, "owner_id", None) == user.id:
        return StaffAccess(is_owner=True)

    from .models import StaffMember

    qs = StaffMember.objects.for_site(site).filter(is_active=True, is_deleted=False)
    member = qs.filter(user=user).first()
    if member is None:
        email = (getattr(user, "email", "") or "").strip().lower()
        member = qs.filter(email=email).first() if email else None
        if member is not None and member.user_id is None:
            member.user = user
            try:
                member.save(update_fields=["user", "updated_at"])
            except Exception:
                pass
    if member is None:
        return None
    return StaffAccess(is_owner=False, permissions=member.permissions or {})


# --------------------------------------------------------------------------
# Form parsing (owner's staff editor)
# --------------------------------------------------------------------------
def parse_permissions(post) -> dict:
    """Read ``perm_<page>_<action>`` checkboxes into ``{page: [actions]}``.

    Any page granted an action beyond view implicitly gains view too (you can't
    edit what you can't see). Pages with no actions are omitted entirely.
    """
    pages = assignable_page_keys()
    out: dict[str, list[str]] = {}
    for page in pages:
        acts = [a for a in ACTION_KEYS if post.get(f"perm_{page}_{a}")]
        if not acts:
            continue
        if "view" not in acts:
            acts = ["view"] + acts
        # Keep canonical order.
        out[page] = [a for a in ACTION_KEYS if a in acts]
    return out


def accessible_sites(user):
    """Every store a user can reach as staff (not the ones they own).

    Used to fold a staff member's stores into the dashboard/profile switcher
    alongside the ones they own.
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return []
    from tenancy.models import Site

    from .models import StaffMember

    email = (getattr(user, "email", "") or "").strip().lower()
    memberships = (
        StaffMember.all_objects.filter(is_active=True, is_deleted=False)
        .filter(models_q_user_or_email(user, email))
        .values_list("site_id", flat=True)
    )
    site_ids = set(memberships)
    if not site_ids:
        return []
    from core.queries import sorted_rows

    # Runs on every dashboard page (store switcher). No index serves this
    # order for a pk list, so sort the keys only and load full rows after:
    # sorting whole Site rows (their ``content`` JSON included) can exceed
    # MySQL's sort buffer (error 1038).
    return sorted_rows(
        Site.objects.filter(pk__in=site_ids, is_deleted=False)
        .exclude(owner=user)
        .order_by("-created_at")
    )


def models_q_user_or_email(user, email):
    from django.db.models import Q

    q = Q(user=user)
    if email:
        q |= Q(email=email)
    return q
