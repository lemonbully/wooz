"""Staff & admins  the owner adds team members and scopes their access.

Owner-only. An owner adds a person by name + email and ticks, per store page,
which actions (view / create / edit / delete) that person may perform. The
number of members is capped by the subscription's team-collaborators grant. A
member then signs in with their own account and reaches the store restricted to
exactly what was ticked (enforced by :mod:`commerce.staff` /
:class:`commerce.middleware.StaffAccessGateMiddleware`).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from billing.entitlements import team_allowance_for_user
from commerce import activity
from commerce import staff as staff_mod
from commerce.context import get_owned_site, store_context
from commerce.models import StaffMember


def _require_owner(request, site):
    """Staff management is owner-only. The gate middleware already blocks staff
    from these routes; this is the defence-in-depth check inside the view."""
    access = getattr(request, "store_access", None)
    if access is not None and not access.is_owner:
        raise Http404("No such page.")


def _perm_groups(perms) -> list[dict]:
    """The assignable pages grouped by sidebar section, each page carrying a
    boolean per action pre-set from ``perms`` (empty for a blank add form).

    Shaped this way  flat named booleans, not a keyed map  so the template's
    checkbox grid needs no dict-by-variable-key lookup.
    """
    perms = perms if isinstance(perms, dict) else {}
    groups: list[dict] = []
    by_label: dict[str, dict] = {}
    for page in staff_mod.assignable_pages():
        g = page["group"]
        if g not in by_label:
            by_label[g] = {"label": g, "pages": []}
            groups.append(by_label[g])
        granted = perms.get(page["key"]) or []
        by_label[g]["pages"].append(
            {
                "key": page["key"],
                "label": page["label"],
                "view": "view" in granted,
                "create": "create" in granted,
                "edit": "edit" in granted,
                "delete": "delete" in granted,
            }
        )
    return groups


def _member_rows(site):
    """Existing members, each with a pre-filled permission grid for editing."""
    rows = []
    for m in StaffMember.objects.for_site(site).filter(is_deleted=False):
        rows.append({"obj": m, "perm_groups": _perm_groups(m.permissions)})
    return rows


def _editor_context(request, site):
    allowance = team_allowance_for_user(getattr(site, "owner", None))
    members = _member_rows(site)
    used = len(members)
    context = store_context(request, site, "")
    context.update(
        {
            "members": members,
            "blank_perm_groups": _perm_groups({}),
            "allowance": allowance,
            "used": used,
            "remaining": max(0, allowance - used),
            "can_add": used < allowance,
            # Owner counts as a user too  surface the "X of Y" including them.
            "total_users": used + 1,
            "total_allowed": allowance + 1,
        }
    )
    return context


@login_required
def staff(request, site_id):
    site = get_owned_site(request, site_id)
    _require_owner(request, site)

    if request.method == "POST":
        return _add_member(request, site)

    return render(request, "store/staff.html", _editor_context(request, site))


def _add_member(request, site):
    name = (request.POST.get("name") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()

    errors = []
    if not name:
        errors.append("Enter the person's name.")
    try:
        validate_email(email)
    except ValidationError:
        errors.append("Enter a valid email address.")

    owner_email = (getattr(getattr(site, "owner", None), "email", "") or "").strip().lower()
    if email and email == owner_email:
        errors.append("You already have full access as the store owner.")

    permissions = staff_mod.parse_permissions(request.POST)
    if not permissions:
        errors.append("Give this person access to at least one page.")

    allowance = team_allowance_for_user(getattr(site, "owner", None))
    used = StaffMember.objects.for_site(site).filter(is_deleted=False).count()
    if used >= allowance:
        errors.append(
            "You've reached your plan's team-member limit. Upgrade your plan to add more."
            if allowance
            else "Your plan doesn't include team members. Upgrade to add staff."
        )

    # A previously-removed member with this email can be revived rather than
    # colliding with the (site, email) uniqueness on the live rows. The scoped
    # manager returns soft-deleted rows too (it never auto-excludes them).
    existing = (
        StaffMember.objects.for_site(site).filter(email=email).first() if email else None
    )
    if existing is not None and not existing.is_deleted:
        errors.append("That email is already a member of this store.")

    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect("store:staff", site_id=site.id)

    if existing is not None:
        existing.is_deleted = False
        existing.deleted_at = None
        existing.name = name
        existing.email = email
        existing.permissions = permissions
        existing.is_active = True
        existing.user = None
        existing.save()
        member = existing
    else:
        member = StaffMember.objects.create(
            site=site, name=name, email=email, permissions=permissions, is_active=True
        )

    activity.record(
        "store.staff.added", request=request, site=site,
        target=member, target_kind="StaffMember", label=member.display_name,
    )
    messages.success(request, f"{member.display_name} can now access this store.")
    return redirect("store:staff", site_id=site.id)


@login_required
@require_POST
def staff_edit(request, site_id, member_id):
    site = get_owned_site(request, site_id)
    _require_owner(request, site)
    member = get_object_or_404(
        StaffMember.objects.for_site(site).filter(is_deleted=False), pk=member_id
    )

    name = (request.POST.get("name") or "").strip()
    if name:
        member.name = name
    member.is_active = bool(request.POST.get("is_active"))
    permissions = staff_mod.parse_permissions(request.POST)
    if not permissions:
        messages.error(request, "Give this person access to at least one page.")
        return redirect("store:staff", site_id=site.id)
    member.permissions = permissions
    member.save()

    activity.record(
        "store.staff.updated", request=request, site=site,
        target=member, target_kind="StaffMember", label=member.display_name,
    )
    messages.success(request, f"{member.display_name}'s access updated.")
    return redirect("store:staff", site_id=site.id)


@login_required
@require_POST
def staff_remove(request, site_id, member_id):
    site = get_owned_site(request, site_id)
    _require_owner(request, site)
    member = get_object_or_404(
        StaffMember.objects.for_site(site).filter(is_deleted=False), pk=member_id
    )
    label = member.display_name
    member.soft_delete()
    activity.record(
        "store.staff.removed", request=request, site=site,
        target=member, target_kind="StaffMember", label=label,
    )
    messages.success(request, f"{label} no longer has access to this store.")
    return redirect("store:staff", site_id=site.id)
