"""Register OneWooz's models with Django's admin (mounted at arcane…/db/).

The admin is auto-discovered by ``django.contrib.admin`` on startup. Every model
in the project's own apps is registered with :class:`TenantSafeModelAdmin`, whose
querysets read through the unscoped ``all_objects`` manager  without that,
``TenantScopedModel`` list pages would raise, because the scoped default manager
refuses to evaluate unless narrowed with ``.for_site()``.

Access is gated upstream by ``custom_admin.middleware.AdminAccessMiddleware``
(staff account + a session from the email/password + emailed-OTP flow), so this
module only concerns *what* is manageable, not *who* may reach it.
"""

from __future__ import annotations

import json
import threading

from urllib.parse import urlencode

from django import forms
from django.apps import apps
from django.contrib import admin, messages
from django.contrib.admin.sites import AlreadyRegistered
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify

from billing.models import (
    INVITE_TTL_MINUTES,
    ActivityPenalty,
    AgreementAcceptance,
    InvoiceDetails,
    CommerceBillDetails,
    CommerceFeeBand,
    CommissionTier,
    Discount,
    PlanFeature,
    PlatformPaymentAccount,
    PricingFeature,
    SubscriptionInvite,
    SubscriptionOrder,
    SubscriptionPlan,
    TemplatePurchase,
    UserAgreement,
    WebsiteTemplate,
)
from commerce.models import (
    Bill,
    BillingConfig,
    Customer,
    CustomerAddress,
    GoogleMerchantConfig,
    GoogleMerchantProductStatus,
    ProductFieldTooltip,
)
from accounts.models import (
    AdminOperator,
    DashboardUser,
    DjangoSuperuser,
    Notification,
    # PushSubscription,  # BROWSER PUSH: DISABLED (see PushSubscriptionAdmin below)
    User,
)
from tenancy.models import BusinessCategory, BusinessSubCategory, ReservedSubdomain, Site
from ud.models import BusinessHubPost, BusinessHubReceipt, SupportTicket
from web.models import (
    CustomerTestimonial,
    DemoSiteLink,
    LegalPolicy,
    MarketingBlog,
    MarketingCoreChildPage,
    MarketingCorePage,
    MarketingCoreSubpage,
    MarketingPageSeo,
    MarketingSeoSettings,
)

from core.queries import defer_related_heavy_fields, without_heavy_fields

from . import marketing_content
from .widgets import HtmlContentWidget, TinyMCEWidget, editor_attrs

# Only the project's own apps  never Django's own tables (sessions, log, etc.).
APP_LABELS = {
    "accounts",
    "tenancy",
    "commerce",
    "storefront",
    "web",
    "billing",
    "ud",
    "core",
    "custom_admin",
    "outreach",
    "marketplace",
}


class LeanModelAdmin(admin.ModelAdmin):
    """``ModelAdmin`` that keeps unbounded columns out of sorted admin queries.

    Two admin paths load *other* models' full rows and let MySQL sort them:

    * Foreign-key / many-to-many dropdowns list every row of the target model
      in its default order. A ``site`` dropdown therefore sorts every store's
      whole ``content`` JSON, which overflows MySQL's sort buffer (error 1038).
      The dropdown only shows ``pk`` and ``__str__``, so the target's heavy
      columns are deferred.
    * ``list_select_related`` joins (e.g. ``site``) add the joined model's
      heavy columns to every changelist row, and sorting the list by that
      column then sorts them. The join is kept; its heavy columns are deferred.

    A deferred column is still loaded on access, so nothing can render wrong.
    """

    def _lean_formfield(self, formfield):
        queryset = getattr(formfield, "queryset", None) if formfield is not None else None
        if queryset is not None and hasattr(queryset, "model"):
            formfield.queryset = without_heavy_fields(queryset)
        return formfield

    def _lean_related_joins(self, queryset):
        related = self.list_select_related
        if isinstance(related, (list, tuple)) and related:
            return defer_related_heavy_fields(queryset, *related)
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        return self._lean_formfield(super().formfield_for_foreignkey(db_field, request, **kwargs))

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        return self._lean_formfield(super().formfield_for_manytomany(db_field, request, **kwargs))

    def get_queryset(self, request):
        return self._lean_related_joins(super().get_queryset(request))


class TenantSafeModelAdmin(LeanModelAdmin):
    """List/CRUD admin that works for tenant-scoped models.

    ``TenantScopedModel`` exposes an unscoped ``all_objects`` manager; the admin
    reads through it so a changelist shows rows across every store. Models that
    are not tenant-scoped fall back to their default manager unchanged.
    """

    list_per_page = 50

    def get_queryset(self, request):
        manager = getattr(self.model, "all_objects", None) or self.model._default_manager
        qs = manager.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return self._lean_related_joins(qs)


def _register_all() -> None:
    for model in apps.get_models():
        if model._meta.app_label not in APP_LABELS:
            continue
        if admin.site.is_registered(model):
            continue

        attrs: dict = {}
        field_names = {f.name for f in model._meta.fields}
        # Never expose an editable password hash: a plain save through the generic
        # form would store the typed value unhashed. Keep it visible but locked.
        if "password" in field_names:
            attrs["readonly_fields"] = ("password",)

        admin_cls = type(f"{model.__name__}Admin", (TenantSafeModelAdmin,), attrs)
        try:
            admin.site.register(model, admin_cls)
        except AlreadyRegistered:
            pass


# ---------------------------------------------------------------------------
# Users  one table, presented as three segregated role lists
# ---------------------------------------------------------------------------
# The project has exactly one authenticated account model (accounts.User). To
# keep the operator's mental model clean, the admin splits it into three
# filtered proxy listings  store owners, subdomain admin operators, and Django
# superusers  instead of one combined "Users" pile. Nothing here alters the
# schema or the auth model; these are pure admin views over the same rows.
#
# Every one of these admins delegates its permission checks to the *base*
# ``accounts.*_user`` permissions the operator already holds (and a superuser
# bypasses all of them). So whoever could see or edit users before this change
# can still see and edit every list after it  there is no access regression,
# and no operator, owner, or superuser loses any capability.


class _UserRoleAdmin(LeanModelAdmin):
    """Shared admin for a single role-slice of the users table.

    Subclasses set ``role_filter`` (the flag combination that defines the role)
    and, optionally, ``role_defaults`` (flags pre-ticked on the add form so a
    new account lands in this same list).
    """

    #: {field: value} narrowing the changelist to this role. Set by subclasses.
    role_filter: dict = {}
    #: Initial flag values for the add form, so a new row belongs to this list.
    role_defaults: dict = {}

    list_display = ("email", "full_name", "is_active", "is_staff", "is_superuser", "created_at")
    list_filter = ("is_active",)
    search_fields = ("email", "full_name", "phone")
    ordering = ("email",)
    # The password hash is shown but never editable through the generic form 
    # a plain save would otherwise store the typed value unhashed. Matches the
    # safeguard the auto-registrar applies to every model carrying a password.
    readonly_fields = ("password", "last_login", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(**self.role_filter)

    def get_changeform_initial_data(self, request):
        data = super().get_changeform_initial_data(request)
        data.update(self.role_defaults)
        return data

    # -- Permissions: delegate to the real accounts.User permissions ---------
    # (A superuser passes every check regardless; this only matters for a
    # non-superuser operator who was granted user permissions.)
    @staticmethod
    def _may(request, action: str) -> bool:
        return request.user.has_perm(f"accounts.{action}_user")

    def has_view_permission(self, request, obj=None):
        return self._may(request, "view") or self._may(request, "change")

    def has_add_permission(self, request):
        return self._may(request, "add")

    def has_change_permission(self, request, obj=None):
        return self._may(request, "change")

    def has_delete_permission(self, request, obj=None):
        return self._may(request, "delete")

    def has_module_permission(self, request):
        return any(
            self._may(request, action) for action in ("view", "change", "add", "delete")
        )


@admin.register(DashboardUser)
class DashboardUserAdmin(_UserRoleAdmin):
    """Store owners  the SaaS customers who sign in to the dashboard.

    Adds an **Impersonate** action so a verified admin can step into an owner's
    dashboard and operate it exactly as that owner would (see
    :mod:`accounts.impersonation`). The button mints a short-lived signed
    hand-off token and redirects to the app surface, which establishes the
    impersonation session there  never touching this admin session.
    """

    role_filter = {"is_staff": False}
    role_defaults = {"is_active": True, "is_staff": False, "is_superuser": False}
    list_display = _UserRoleAdmin.list_display + ("account_state", "impersonate_action")
    list_filter = ("is_active", ("account_deleted_at", admin.EmptyFieldListFilter))
    readonly_fields = _UserRoleAdmin.readonly_fields + (
        "account_deleted_at", "account_deletion_reason", "account_deletion_plan",
        "account_deletion_sites", "account_closed_at", "account_restored_at",
    )

    @admin.display(description="Account")
    def account_state(self, obj):
        if obj.account_closed_at:
            return "Closed (deleted)"
        if obj.account_deleted_at:
            from accounts import deletion

            return f"Deleted, recoverable until {deletion.recover_until(obj.account_deleted_at):%d %b %Y}"
        return "Active" if obj.is_active else "Disabled"

    def get_urls(self):
        urls = super().get_urls()
        info = (self.model._meta.app_label, self.model._meta.model_name)
        custom = [
            path(
                "<path:object_id>/impersonate/",
                self.admin_site.admin_view(self.impersonate_view),
                name="%s_%s_impersonate" % info,
            ),
        ]
        return custom + urls

    @admin.display(description="Impersonate")
    def impersonate_action(self, obj):
        if obj.is_staff or not obj.is_active:
            return "—"
        url = reverse(
            "admin:%s_%s_impersonate" % (self.model._meta.app_label, self.model._meta.model_name),
            args=[obj.pk],
        )
        return format_html('<a class="button" href="{}">Impersonate</a>', url)

    def impersonate_view(self, request, object_id):
        """Mint the hand-off token and bounce to the app surface's entry point."""
        from accounts import impersonation
        from core import audit
        from core.hosts import Surface
        from core.links import surface_url

        changelist = reverse(
            "admin:%s_%s_changelist" % (self.model._meta.app_label, self.model._meta.model_name)
        )
        if not (request.user.is_authenticated and request.user.is_staff):
            raise PermissionDenied

        try:
            target = self.get_queryset(request).filter(pk=object_id).first()
        except (ValueError, ValidationError):
            target = None
        if target is None or target.is_staff or not target.is_active:
            self.message_user(
                request, "That account cannot be impersonated.", level=messages.ERROR
            )
            return HttpResponseRedirect(changelist)

        # "Stop impersonating" brings the admin back to this owners list.
        token = impersonation.mint_token(
            request.user, target, return_url=request.build_absolute_uri(changelist)
        )
        audit.record(
            "admin.impersonation.link",
            request=request,
            actor=request.user,
            actor_kind="admin",
            target=target,
            target_kind="User",
            target_label=target.email,
        )
        url = surface_url(Surface.APP, "/impersonate/enter/", request=request)
        return HttpResponseRedirect(url + "?" + urlencode({"t": token}))


@admin.register(AdminOperator)
class AdminOperatorAdmin(_UserRoleAdmin):
    """Subdomain (arcane) admin operators: staff accounts that are not full
    superusers."""

    role_filter = {"is_staff": True, "is_superuser": False}
    role_defaults = {"is_active": True, "is_staff": True, "is_superuser": False}


@admin.register(DjangoSuperuser)
class DjangoSuperuserAdmin(_UserRoleAdmin):
    """Full Django superusers  the accounts that may manage the DB and models."""

    role_filter = {"is_superuser": True}
    role_defaults = {"is_active": True, "is_staff": True, "is_superuser": True}


@admin.register(User)
class _BaseUserHiddenAdmin(_UserRoleAdmin):
    """The underlying ``accounts.User`` stays registered  so links such as the
    Notification recipient's raw-id lookup (``admin:accounts_user_changelist``)
    keep resolving  but it is hidden from the admin index, because the three
    role lists above replace the single combined "Users" entry.
    """

    role_filter = {}  # no narrowing: this is the raw table, reachable only by URL

    def get_model_perms(self, request):
        # An empty perms dict removes the index tile without unregistering the
        # model, leaving its change/history/changelist URLs intact.
        return {}

    def get_search_results(self, request, queryset, search_term):
        # The Business Hub "customers" picker autocompletes against this admin;
        # offer only active store owners there (never staff/operators).
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if request.GET.get("model_name") == "businesshubpost":
            queryset = queryset.filter(is_staff=False, is_active=True)
        return queryset, may_have_duplicates


# ---------------------------------------------------------------------------
# Stores, with deletion status and a support "Restore" action
# ---------------------------------------------------------------------------
# A deleted store (owner: Store settings -> Delete this store) is only soft
# deleted and stays recoverable for settings.ACCOUNT_DELETION_RETENTION_DAYS.
# Support restores it from here when the owner asks (accounts.deletion). Stores
# that went down with a deleted account come back when the owner signs in, so
# the action refuses those.


class _SiteDeletionFilter(admin.SimpleListFilter):
    title = "deletion"
    parameter_name = "deletion"

    def lookups(self, request, model_admin):
        return (
            ("live", "Live (not deleted)"),
            ("deleted", "Deleted by owner"),
            ("with_account", "Deleted with account"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "live":
            return queryset.filter(is_deleted=False)
        if value == "deleted":
            return queryset.filter(is_deleted=True, deleted_with_account=False)
        if value == "with_account":
            return queryset.filter(is_deleted=True, deleted_with_account=True)
        return queryset


@admin.register(Site)
class SiteAdmin(LeanModelAdmin):
    list_display = ("name", "slug", "owner", "status", "deletion_state", "created_at")
    list_filter = (_SiteDeletionFilter, "status", "business_model")
    search_fields = ("name", "slug", "owner__email", "custom_domain")
    list_select_related = ("owner",)
    raw_id_fields = ("owner",)
    readonly_fields = ("is_deleted", "deleted_at", "deleted_with_account", "created_at", "updated_at")
    actions = ("restore_deleted_stores",)

    def get_queryset(self, request):
        # Never load (or sort) the content JSON for the list (MySQL error 1038).
        return super().get_queryset(request).defer("content")

    @admin.display(description="Deletion")
    def deletion_state(self, obj):
        if not obj.is_deleted:
            return "Live"
        from accounts import deletion

        until = deletion.recover_until(obj.deleted_at)
        how = "with account" if obj.deleted_with_account else "by owner"
        if deletion.is_lapsed(obj.deleted_at):
            return f"Deleted {how}, window ended {until:%d %b %Y}"
        return f"Deleted {how}, recoverable until {until:%d %b %Y}"

    @admin.action(description="Restore selected deleted stores")
    def restore_deleted_stores(self, request, queryset):
        from accounts import deletion

        done, refused = 0, []
        for site in queryset.select_related("owner"):
            ok, message = deletion.restore_site(site, request=request, actor=request.user)
            if ok:
                done += 1
            elif site.is_deleted:
                refused.append(f"{site.name}: {message}")
        if done:
            self.message_user(request, f"Restored {done} store{'s' if done != 1 else ''}.", level=messages.SUCCESS)
        for line in refused[:10]:
            self.message_user(request, line, level=messages.WARNING)


# ---------------------------------------------------------------------------
# Business taxonomy  categories + subcategories (fee-calculator style)
# ---------------------------------------------------------------------------
class BusinessSubCategoryInline(admin.TabularInline):
    model = BusinessSubCategory
    extra = 1
    fields = ("name", "is_active", "order")
    ordering = ("order", "name")


@admin.register(BusinessCategory)
class BusinessCategoryAdmin(LeanModelAdmin):
    list_display = ("name", "is_active", "order", "slug")
    list_editable = ("is_active", "order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = (BusinessSubCategoryInline,)


@admin.register(BusinessSubCategory)
class BusinessSubCategoryAdmin(LeanModelAdmin):
    list_display = ("name", "category", "is_active", "order")
    list_filter = ("category", "is_active")
    search_fields = ("name", "category__name")


@admin.register(ReservedSubdomain)
class ReservedSubdomainAdmin(LeanModelAdmin):
    """Subdomain labels no store may use (checked in addition to a built-in set)."""

    list_display = ("keyword", "is_active", "note", "created_at")
    list_editable = ("is_active",)
    search_fields = ("keyword", "note")
    list_filter = ("is_active",)
    ordering = ("keyword",)


@admin.register(DemoSiteLink)
class DemoSiteLinkAdmin(LeanModelAdmin):
    """Operator-curated "see a live store" links. Each active one is a card on
    /demo/ (sorted by order) and turns the home hero button into "See demo"."""

    list_display = ("label", "order", "is_active", "kind", "url")
    list_editable = ("order", "is_active", "kind")
    list_filter = ("kind", "is_active")
    search_fields = ("label", "url", "description")
    ordering = ("order", "created_at")
    fieldsets = (
        (None, {"fields": ("url", "label", "image", "order", "is_active")}),
        (
            "Pricing page",
            {
                "classes": ("collapse",),
                "fields": ("kind", "description"),
                "description": "Optional: how this link is grouped and described on the pricing page.",
            },
        ),
    )


# ---------------------------------------------------------------------------
# Marketing-site CMS  core pages (page → subpage → child) + blog
# ---------------------------------------------------------------------------
# Content is pasted HTML (written in ChatGPT, Claude or by hand), not TinyMCE:
# the paste-HTML editor (HtmlContentWidget) previews it with the live site
# styles and uploads images; models clean it on save (web.content_html).
_CONTENT_HELP = (
    "Paste HTML: headings (h2, h3), paragraphs, lists, tables, quotes, links and images. "
    "No styling needed: inline styles, classes and scripts are removed on save and the "
    "site's theme styles everything. Upload images below and paste their links."
)


def _rich_form(model_cls):
    """A ModelForm whose ``content`` field is the paste-HTML editor."""
    meta = type(
        "Meta",
        (),
        {
            "model": model_cls,
            "fields": "__all__",
            "widgets": {"content": HtmlContentWidget()},
            "help_texts": {"content": _CONTENT_HELP},
        },
    )
    return type(f"{model_cls.__name__}Form", (forms.ModelForm,), {"Meta": meta})


# -- One-page tree editor --------------------------------------------------
# The whole page → subpage → child tree is created, edited and reordered on a
# single screen (the core-page changelist redirects to it), each page an
# accordion whose body holds its fields and its nested subpages/children 
# mirroring the storefront owner's content editor. Subpages and child pages are
# therefore never managed on their own admin screens (hidden from the index).


def _node_json(obj, **extra):
    """Serialise one tree node for the editor to hydrate / reconcile against."""
    data = {
        "id": str(obj.id),
        "title": obj.title,
        "slug": obj.slug,
        "content": obj.content,
        "is_published": obj.is_published,
    }
    if hasattr(obj, "show_in_footer"):
        data["show_in_footer"] = obj.show_in_footer
    data.update(extra)
    return data


def _serialize_tree():
    """The entire marketing core-page tree (drafts included), ordered, for the
    editor's hydration.

    Three flat queries sorted in Python: the rows carry long HTML bodies, so an
    ORDER BY over them is the wide-row sort MySQL rejects (error 1038)."""
    def ordered(rows):
        return sorted(rows, key=lambda r: (r.order, (r.title or "").lower()))

    pages = ordered(MarketingCorePage.objects.order_by())
    subs = ordered(MarketingCoreSubpage.objects.order_by())
    children = ordered(MarketingCoreChildPage.objects.order_by())
    kids_of = {}
    for child in children:
        kids_of.setdefault(child.subpage_id, []).append(child)
    subs_of = {}
    for sub in subs:
        subs_of.setdefault(sub.core_page_id, []).append(sub)

    out = []
    for page in pages:
        subs_out = [
            _node_json(sub, children=[_node_json(c) for c in kids_of.get(sub.id, [])])
            for sub in subs_of.get(page.id, [])
        ]
        out.append(_node_json(page, subpages=subs_out))
    return out


def _dedupe_slug(used, provided, title, fallback):
    """A slug unique within ``used`` (one sibling set), from the slug or title."""
    base = (slugify(provided or "") or slugify(title or "") or fallback)[:130]
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _sync_level(model, parent_kwargs, items, fallback):
    """Create/update/delete one level of the tree under ``parent_kwargs``.

    Two passes so sibling slugs never collide mid-save: every posted row is first
    saved with a throwaway-unique slug (its own id) and stale rows are pruned,
    then real de-duplicated slugs are written. Returns the saved rows in the
    order they were posted (parallel to ``items``).
    """
    existing = {str(o.id): o for o in model.objects.filter(**parent_kwargs)}
    saved, kept = [], []
    for index, item in enumerate(items):
        obj = existing.get(str(item.get("id") or "")) or model(**parent_kwargs)
        obj.title = (item.get("title") or "").strip() or "Untitled"
        obj.content = item.get("content") or ""
        obj.is_published = bool(item.get("is_published"))
        if hasattr(obj, "show_in_footer"):
            obj.show_in_footer = bool(item.get("show_in_footer"))
        obj.order = index
        obj.slug = str(obj.id)  # temporary  guaranteed unique, replaced below
        obj.save()
        kept.append(obj.id)
        saved.append(obj)
    model.objects.filter(**parent_kwargs).exclude(id__in=kept).delete()

    used = set()
    for obj, item in zip(saved, items):
        obj.slug = _dedupe_slug(used, item.get("slug"), obj.title, fallback)
        used.add(obj.slug)
        obj.save(update_fields=["slug", "updated_at"])
    return saved


def _sync_tree(pages_in):
    """Apply the posted tree to the three models; return it re-serialised."""
    pages = _sync_level(MarketingCorePage, {}, pages_in, "page")
    result = []
    for page, page_in in zip(pages, pages_in):
        subs = _sync_level(
            MarketingCoreSubpage, {"core_page": page}, page_in.get("subpages") or [], "subpage"
        )
        subs_out = []
        for sub, sub_in in zip(subs, page_in.get("subpages") or []):
            children = _sync_level(
                MarketingCoreChildPage, {"subpage": sub}, sub_in.get("children") or [], "page"
            )
            subs_out.append(_node_json(sub, children=[_node_json(c) for c in children]))
        result.append(_node_json(page, subpages=subs_out))
    return result


@admin.register(MarketingCorePage)
class MarketingCorePageAdmin(LeanModelAdmin):
    form = _rich_form(MarketingCorePage)
    list_display = ("title", "is_published", "show_in_footer", "order", "slug")
    list_editable = ("is_published", "show_in_footer", "order")
    search_fields = ("title", "content")
    prepopulated_fields = {"slug": ("title",)}

    # -- the single-page tree editor ---------------------------------------
    def get_urls(self):
        # Custom routes first, so "tree/" is not swallowed by the object-id
        # catch-all that Django's ModelAdmin appends.
        mine = [
            path(
                "tree/",
                self.admin_site.admin_view(self.tree_view),
                name="web_marketingcorepage_tree",
            ),
            path(
                "tree/save/",
                self.admin_site.admin_view(self.tree_save),
                name="web_marketingcorepage_tree_save",
            ),
            # Paste-HTML editor endpoints, shared with the blog post form.
            path(
                "content/preview/",
                self.admin_site.admin_view(marketing_content.preview),
                name="web_content_preview",
            ),
            path(
                "content/upload-image/",
                self.admin_site.admin_view(marketing_content.upload_image),
                name="web_content_upload_image",
            ),
        ]
        return mine + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        # The tree editor is the way core pages are managed; the plain changelist
        # stays reachable with ?raw=1 for the occasional direct row edit.
        if not request.GET.get("raw"):
            return redirect("admin:web_marketingcorepage_tree")
        return super().changelist_view(request, extra_context)

    def tree_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            "title": "Core pages",
            "opts": self.model._meta,
            "tree_data": _serialize_tree(),
            "save_url": reverse("admin:web_marketingcorepage_tree_save"),
            "raw_changelist_url": reverse("admin:web_marketingcorepage_changelist") + "?raw=1",
            # data-preview-url → editor.preview_url etc., for the template.
            "editor": {
                k.removeprefix("data-").replace("-", "_"): v
                for k, v in editor_attrs(reverse, static).items()
            },
        }
        return render(request, "admin/web/marketingcorepage/tree.html", context)

    def tree_save(self, request):
        if request.method != "POST" or not self.has_change_permission(request):
            return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON.")
        with transaction.atomic():
            pages = _sync_tree(payload.get("pages") or [])
        return JsonResponse(
            {"ok": True, "pages": pages, "saved_at": timezone.now().strftime("%H:%M:%S")}
        )


class _HiddenFromIndexAdmin(LeanModelAdmin):
    """Registered  so change/history URLs still resolve  but hidden from the
    admin index. Subpages and child pages are managed from the core-page tree
    editor, never on their own screens."""

    def get_model_perms(self, request):
        return {}


@admin.register(MarketingCoreSubpage)
class MarketingCoreSubpageAdmin(_HiddenFromIndexAdmin):
    form = _rich_form(MarketingCoreSubpage)
    list_display = ("title", "core_page", "is_published", "order")
    list_filter = ("core_page", "is_published")
    search_fields = ("title", "content", "core_page__title")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(MarketingCoreChildPage)
class MarketingCoreChildPageAdmin(_HiddenFromIndexAdmin):
    form = _rich_form(MarketingCoreChildPage)
    list_display = ("title", "subpage", "is_published", "order")
    list_filter = ("subpage", "is_published")
    search_fields = ("title", "content", "subpage__title")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(MarketingBlog)
class MarketingBlogAdmin(LeanModelAdmin):
    form = _rich_form(MarketingBlog)
    list_display = ("title", "author", "is_published", "published_at", "slug")
    list_editable = ("is_published",)
    list_filter = ("is_published",)
    search_fields = ("title", "excerpt", "content", "author")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "published_at"


# ---------------------------------------------------------------------------
# Business Hub  News / Help / Important posts for owners' dashboards
# ---------------------------------------------------------------------------
class BusinessHubPostForm(forms.ModelForm):
    class Meta:
        model = BusinessHubPost
        fields = "__all__"
        widgets = {"description": TinyMCEWidget()}

    def clean_video_url(self):
        from ud.models import video_link_embed

        url = (self.cleaned_data.get("video_url") or "").strip()
        if url and not video_link_embed(url):
            raise forms.ValidationError(
                "Paste a YouTube or Vimeo video link, or upload the video file above instead."
            )
        return url

    def clean(self):
        data = super().clean()
        if not data.get("send_to_all") and not data.get("recipients"):
            raise forms.ValidationError(
                "Choose at least one customer, or tick “All users” to send this to everyone."
            )
        return data


@admin.register(BusinessHubPost)
class BusinessHubPostAdmin(LeanModelAdmin):
    """Author a Business Hub post. Pick its type (News / Help / Important), a
    title and a description, then either tick "All users" or choose one or more
    customers. On save every recipient gets it in their dashboard's Business
    Hub, a bell notification and an email. Re-saving (e.g. after adding more
    customers) only sends to owners who haven't received it yet."""

    form = BusinessHubPostForm
    list_display = ("title", "kind", "audience", "is_published", "sent_count", "created_at")
    list_filter = ("kind", "send_to_all", "is_published", "created_at")
    search_fields = ("title", "description")
    autocomplete_fields = ("recipients",)
    readonly_fields = ("delivery_status",)
    fieldsets = (
        (None, {"fields": ("kind", "title", "description", "is_published")}),
        ("Video", {
            "fields": ("video", "video_url"),
            "description": "Optional. Upload a video file or paste a YouTube / Vimeo "
            "link; it plays inside the post, below the description.",
        }),
        ("Who receives it", {
            "fields": ("send_to_all", "recipients"),
            "description": "Tick “All users” to send to every store owner, or pick one "
            "or more customers. Each recipient gets it in their dashboard, as a "
            "notification and by email.",
        }),
        ("Delivery", {"fields": ("delivery_status",)}),
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "recipients":
            kwargs["queryset"] = User.objects.filter(is_staff=False, is_active=True).order_by("email")
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        post = form.instance
        if not post.is_published:
            return
        from ud.tasks import deliver_business_hub_post

        post_id = str(post.pk)
        transaction.on_commit(lambda: deliver_business_hub_post.delay(post_id))
        from ud import hub

        pending = hub.target_users(post).exclude(business_hub_receipts__post=post).count()
        if pending:
            self.message_user(
                request,
                f"Sending “{post.title}” to {pending} owner{'s' if pending != 1 else ''}: "
                "dashboard, notification and email.",
            )

    @admin.display(description="Audience")
    def audience(self, obj):
        return "All users" if obj.send_to_all else f"{obj.recipients.count()} selected"

    @admin.display(description="Sent to")
    def sent_count(self, obj):
        return obj.receipts.count()

    @admin.display(description="Delivery status")
    def delivery_status(self, obj):
        if obj is None or obj.pk is None:
            return "Sent to recipients when you save."
        total = obj.receipts.count()
        emailed = obj.receipts.filter(emailed=True).count()
        return f"Delivered to {total} owner(s) · {emailed} email(s) sent."


@admin.register(BusinessHubReceipt)
class BusinessHubReceiptAdmin(LeanModelAdmin):
    """Read-only log of who each Business Hub post reached (hidden from the
    index; reachable from a post's delivery status)."""

    list_display = ("post", "user", "emailed", "created_at")
    list_filter = ("emailed",)
    search_fields = ("post__title", "user__email")

    def get_model_perms(self, request):
        return {}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SupportTicket)
class SupportTicketAdmin(LeanModelAdmin):
    """Support tickets store owners raise from their Business Hub. Set the
    status and write a reply; saving a new reply (or resolving / closing the
    ticket) shows it on the owner's Business Hub and sends them a bell
    notification. What the owner wrote stays read-only."""

    list_display = (
        "reference", "subject", "follow_up_of", "user", "site", "category", "priority", "status", "created_at",
    )
    list_filter = ("status", "priority", "category", "created_at")
    search_fields = ("subject", "message", "user__email", "user__full_name", "site__name")
    list_select_related = ("user", "site", "parent")
    readonly_fields = (
        "reference", "follow_up_of", "user", "site", "subject", "category", "priority_display",
        "message", "attachment_link", "created_at", "replied_at", "resolved_at", "conversation",
    )
    fieldsets = (
        ("Ticket", {"fields": (
            "reference", "follow_up_of", "user", "site", "subject", "category", "priority_display",
            "message", "attachment_link", "created_at",
        )}),
        ("Earlier in this conversation", {"fields": ("conversation",)}),
        ("Your response", {"fields": ("status", "priority", "reply", "replied_at", "resolved_at")}),
    )

    @admin.display(description="Follow-up of")
    def follow_up_of(self, obj):
        from django.urls import reverse as _reverse
        from django.utils.html import format_html

        if not obj.parent_id:
            return "-"
        url = _reverse("admin:ud_supportticket_change", args=[obj.parent_id])
        return format_html('<a href="{}">{}</a>', url, obj.parent.reference)

    @admin.display(description="Conversation")
    def conversation(self, obj):
        """Every message in this ticket's thread before (and after) this one,
        with the replies already sent, so staff answer with full context."""
        from django.urls import reverse as _reverse
        from django.utils.html import format_html, format_html_join

        if not obj.pk:
            return "-"
        root = obj.thread_root
        thread = [root, *root.follow_ups.order_by("created_at")]
        if len(thread) < 2:
            return "No other messages: this ticket has no follow-ups."
        rows = []
        for m in thread:
            label = "This ticket" if m.pk == obj.pk else m.reference
            link = _reverse("admin:ud_supportticket_change", args=[m.pk])
            reply = (
                format_html(
                    '<div style="margin-top:6px;padding-left:10px;border-left:2px solid #4b2bf5">'
                    '<b>OneWooz replied:</b><div style="white-space:pre-line">{}</div></div>',
                    m.reply,
                )
                if (m.reply or "").strip() else ""
            )
            rows.append((link, label, m.get_status_display(), m.created_at.strftime("%d %b %Y %H:%M"), m.message, reply))
        return format_html_join(
            "",
            '<div style="margin:0 0 12px;padding:8px 10px;border:1px solid #ddd">'
            '<div><a href="{}"><b>{}</b></a> · {} · {}</div>'
            '<div style="margin-top:4px;white-space:pre-line">{}</div>{}</div>',
            rows,
        )

    def has_add_permission(self, request):
        return False  # tickets come from owners

    @admin.display(description="Reference")
    def reference(self, obj):
        return obj.reference

    @admin.display(description="Priority (as raised)")
    def priority_display(self, obj):
        return obj.get_priority_display()

    @admin.display(description="Attachment")
    def attachment_link(self, obj):
        from django.utils.html import format_html

        url = obj.attachment_url
        if not url:
            return "None"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, obj.attachment_name or "Download")

    def save_model(self, request, obj, form, change):
        from django.utils import timezone as _tz

        from ud import support

        notify = False
        if change and "reply" in form.changed_data and (obj.reply or "").strip():
            obj.replied_at = _tz.now()
            notify = True
        if "status" in form.changed_data:
            if obj.status in (SupportTicket.Status.RESOLVED, SupportTicket.Status.CLOSED):
                obj.resolved_at = obj.resolved_at or _tz.now()
                notify = True
            else:
                obj.resolved_at = None
        super().save_model(request, obj, form, change)
        if notify:
            support.notify_owner_of_reply(obj)


@admin.register(CustomerTestimonial)
class CustomerTestimonialAdmin(LeanModelAdmin):
    """Customer testimonials for the marketing Customers page, managed on ONE
    screen: every testimonial is an editable row, blank rows add new ones (an
    "Add another" button adds more), and a single Save writes them all. The
    plain changelist stays reachable with ?raw=1."""

    list_display = ("name", "company_name", "industry", "order", "is_active")
    list_editable = ("order", "is_active")
    search_fields = ("name", "company_name", "industry", "review")

    _FIELDS = ("company_name", "industry", "name", "review", "order", "is_active")

    def get_urls(self):
        mine = [
            path(
                "bulk/",
                self.admin_site.admin_view(self.bulk_view),
                name="web_customertestimonial_bulk",
            ),
        ]
        return mine + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        if not request.GET.get("raw"):
            return redirect("admin:web_customertestimonial_bulk")
        return super().changelist_view(request, extra_context)

    def add_view(self, request, form_url="", extra_context=None):
        return redirect("admin:web_customertestimonial_bulk")

    def _formset_class(self):
        return forms.modelformset_factory(
            CustomerTestimonial,
            fields=self._FIELDS,
            extra=3,
            can_delete=True,
            widgets={
                "review": forms.Textarea(attrs={"rows": 3, "cols": 40}),
                "company_name": forms.TextInput(attrs={"size": 18}),
                "industry": forms.TextInput(attrs={"size": 14}),
                "name": forms.TextInput(attrs={"size": 16}),
                "order": forms.NumberInput(attrs={"style": "width:4.5rem"}),
            },
        )

    def bulk_view(self, request):
        from django.contrib import messages
        from django.template.response import TemplateResponse

        if not (self.has_change_permission(request) and self.has_add_permission(request)):
            raise PermissionDenied
        FormSet = self._formset_class()
        queryset = CustomerTestimonial.objects.order_by("order", "created_at")
        if request.method == "POST":
            formset = FormSet(request.POST, queryset=queryset, prefix="t")
            if formset.is_valid():
                with transaction.atomic():
                    formset.save()
                messages.success(request, "Testimonials saved.")
                return redirect("admin:web_customertestimonial_bulk")
            messages.error(request, "Some rows need attention. Check the highlighted fields.")
        else:
            formset = FormSet(queryset=queryset, prefix="t")
        context = {
            **self.admin_site.each_context(request),
            "title": "Customer testimonials",
            "opts": self.model._meta,
            "formset": formset,
            "raw_changelist_url": reverse("admin:web_customertestimonial_changelist") + "?raw=1",
        }
        return TemplateResponse(request, "admin/web/customertestimonial/bulk.html", context)


_policy_admin_local = threading.local()


@admin.register(LegalPolicy)
class LegalPolicyAdmin(LeanModelAdmin):
    """Legal policies for onewooz.com (refund, shipping, cookies…).

    Each published policy is listed in the footer under "Legal" and served at
    /legal/<slug>/ in the same page shell as Privacy and Terms. The slug fills
    in from the name as you type and stays editable. ``content`` uses the same
    free (GPL) TinyMCE editor as the user agreement and the blog."""

    form = _rich_form(LegalPolicy)
    list_display = ("title", "slug", "is_published", "show_in_footer", "order", "updated_at", "view_link")
    list_editable = ("is_published", "show_in_footer", "order")
    list_filter = ("is_published", "show_in_footer")
    search_fields = ("title", "slug", "content")
    prepopulated_fields = {"slug": ("title",)}
    fields = ("title", "slug", "content", "is_published", "show_in_footer", "order")
    ordering = ("order", "title")

    def changelist_view(self, request, extra_context=None):
        # Remembered (per thread) so view_link can build a correct cross-surface
        # URL for this request's host/port and dev path mode.
        _policy_admin_local.request = request
        try:
            return super().changelist_view(request, extra_context)
        finally:
            _policy_admin_local.request = None

    @admin.display(description="Page")
    def view_link(self, obj):
        """Link to the live page on onewooz.com. The admin runs on its own
        urlconf and script prefix, so reverse against the marketing urlconf at
        the root prefix, then point it at the marketing surface."""
        from django.urls import get_script_prefix, set_script_prefix
        from django.utils.html import format_html

        from core import links

        name = "web:page" if obj.is_built_in else "web:policy"
        previous = get_script_prefix()
        try:
            set_script_prefix("/")
            path = reverse(name, kwargs={"slug": obj.slug}, urlconf="onewooz.urls.marketing")
            url = links.surface_url(
                "marketing", path, request=getattr(_policy_admin_local, "request", None)
            )
        except Exception:
            return "-"
        finally:
            set_script_prefix(previous)
        return format_html('<a href="{}" target="_blank" rel="noopener">View</a>', url)


# ---------------------------------------------------------------------------
# Platform subscriptions  plans (with feature bullets), discount codes, orders
# ---------------------------------------------------------------------------
class PlanFeatureInline(admin.TabularInline):
    """The bullet points listed on a plan's pricing card."""

    model = PlanFeature
    extra = 3
    fields = ("text", "value", "included", "order")
    ordering = ("order",)


class CommerceFeeBandForm(forms.ModelForm):
    """One price range row: Minimum value → Maximum value → Commerce fee."""

    class Meta:
        model = CommerceFeeBand
        fields = ("min_price", "max_price", "fee", "order")
        labels = {
            "min_price": "Minimum value (₹)",
            "max_price": "Maximum value (₹)",
            "fee": "Commerce fee (₹)",
            "order": "Order",
        }
        help_texts = {
            "min_price": "Lowest product price in this range (included).",
            "max_price": "Highest product price in this range (included). Leave blank for “this price and above”.",
            "fee": "Charged for each product sold at a price inside this range, on delivery.",
        }


class CommerceFeeBandFormSet(forms.BaseInlineFormSet):
    """Every range must read minimum ≤ maximum, and no two ranges may share a
    price, so each product price can only ever fall in one range."""

    def clean(self):
        super().clean()
        from decimal import Decimal

        from billing.fee_bands import range_label

        rows = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            lo = form.cleaned_data.get("min_price")
            hi = form.cleaned_data.get("max_price")
            fee = form.cleaned_data.get("fee")
            if lo is None and hi is None and fee is None:
                continue
            lo = Decimal(lo or 0)
            if hi is not None and Decimal(hi) < lo:
                form.add_error("max_price", "Maximum value must be the same as or higher than the minimum value.")
                continue
            rows.append((lo, Decimal(hi) if hi is not None else None))
        rows.sort(key=lambda r: r[0])
        for (lo1, hi1), (lo2, hi2) in zip(rows, rows[1:]):
            if hi1 is None or lo2 <= hi1:
                raise ValidationError(
                    f"The price ranges {range_label(lo1, hi1)} and {range_label(lo2, hi2)} overlap. "
                    "Each product price must fall in only one range."
                )


class CommerceFeeBandInline(admin.TabularInline):
    """Commerce fee by PRICE RANGE  how the fee is charged.

    Each row is a minimum value → maximum value range and the commerce fee for
    it, e.g. ₹0 – ₹299 → ₹9, ₹500 – ₹2,000 → ₹20. A product sold at ₹150, ₹200
    or ₹299 is charged ₹9; one at ₹1,200 is charged ₹20. Each delivered order
    line is matched by its selling price (the product variation's price, else
    the product's); coupons never change the range."""

    model = CommerceFeeBand
    form = CommerceFeeBandForm
    formset = CommerceFeeBandFormSet
    extra = 3
    fields = ("min_price", "max_price", "fee", "order")
    ordering = ("order", "min_price")
    verbose_name = "commerce fee price range"
    verbose_name_plural = "Commerce fee by price range (minimum value to maximum value)"


class ActivityPenaltyInline(admin.TabularInline):
    """Activity windows + reactivation penalties, as many as needed.

    Each row is one day window with its own order threshold and penalty  e.g.
    30 days → 30 orders → ₹500, 50 days → 60 orders → ₹800. If the store misses a
    window's threshold it is suspended, and reactivating costs that penalty
    (even on a one-time plan)."""

    model = ActivityPenalty
    extra = 2
    fields = ("window_days", "threshold_orders", "reactivation_penalty", "order")
    ordering = ("order", "window_days")


class SubscriptionPlanForm(forms.ModelForm):
    """Adds a checkbox picker for the free-plan dashboard features, stored as a
    JSON list of feature keys on ``included_features``."""

    included_features = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "FREE plans only: tick the dashboard areas this plan includes. "
            "Everything left unticked shows an “Upgrade” badge and opens an "
            "upgrade wall instead of the page. Leave all unticked to impose no "
            "limit. (Ignored for paid plans  those get everything.)"
        ),
    )
    included_editor_tabs = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "FREE plans only: tick the tabs of the store's Edit-content page this "
            "plan lets the owner edit. Everything left unticked is shown locked "
            "as an “Upgrade” inside the editor. Leave all unticked to allow every "
            "tab. (Ignored for paid plans.)"
        ),
    )

    # ── Email-marketing limits (edited as fields, stored in the JSON block) ──
    em_max_recipients = forms.IntegerField(
        required=False, min_value=0, label="Max recipients per campaign",
        help_text="Cap on recipients in one send. 0 = unlimited.",
    )
    em_monthly_limit = forms.IntegerField(
        required=False, min_value=0, label="Emails per month",
        help_text="Total marketing emails the store may send per calendar month. 0 = unlimited.",
    )
    em_emails_per_minute = forms.IntegerField(
        required=False, min_value=0, label="Emails per minute",
        help_text="Send-pacing ceiling. 0 = unlimited.",
    )
    em_allow_ai = forms.BooleanField(
        required=False, label="Allow AI email builder",
        help_text="Let this plan's owners draft campaign HTML with the AI builder.",
    )
    em_scheduling = forms.ChoiceField(
        required=False,
        label="Campaign scheduling",
        choices=(("datetime", "Allow date & time"), ("simple", "Simple (future date only)")),
        help_text="Whether owners may schedule a precise date+time or only a future date.",
    )

    class Meta:
        model = SubscriptionPlan
        exclude = ("email_marketing",)

    class Media:
        # Reveals the "Free-plan …" fieldsets only when plan type is Free, and the
        # "Email marketing" limits when that feature applies to the plan.
        js = ("js/admin/plan_free_features.js", "js/admin/plan_email_marketing.js")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both vocabularies come straight from what the app actually shows, so
        # they never drift: the sidebar for features, the editor for its tabs.
        from commerce.context import feature_choices
        from commerce.views.editor import editor_tab_choices

        self.fields["included_features"].choices = feature_choices()
        self.fields["included_editor_tabs"].choices = editor_tab_choices()

        # Seed the email-marketing fields from the instance's frozen block.
        from billing.entitlements import normalize_email_marketing

        em = normalize_email_marketing(getattr(self.instance, "email_marketing", None))
        self.fields["em_max_recipients"].initial = em["max_recipients_per_campaign"]
        self.fields["em_monthly_limit"].initial = em["monthly_email_limit"]
        self.fields["em_emails_per_minute"].initial = em["emails_per_minute"]
        self.fields["em_allow_ai"].initial = em["allow_ai_builder"]
        self.fields["em_scheduling"].initial = em["scheduling"]

    def clean_included_features(self):
        return list(self.cleaned_data.get("included_features") or [])

    def clean_included_editor_tabs(self):
        return list(self.cleaned_data.get("included_editor_tabs") or [])

    def save(self, commit=True):
        from billing.entitlements import normalize_email_marketing

        self.instance.email_marketing = normalize_email_marketing(
            {
                "max_recipients_per_campaign": self.cleaned_data.get("em_max_recipients") or 0,
                "monthly_email_limit": self.cleaned_data.get("em_monthly_limit") or 0,
                "emails_per_minute": self.cleaned_data.get("em_emails_per_minute") or 0,
                "allow_ai_builder": self.cleaned_data.get("em_allow_ai") or False,
                "scheduling": self.cleaned_data.get("em_scheduling") or "datetime",
            }
        )
        return super().save(commit=commit)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(LeanModelAdmin):
    form = SubscriptionPlanForm
    list_display = (
        "name", "plan_type", "duration", "price_monthly", "one_time_price",
        "number_of_stores", "number_of_collaborations", "concurrent_login_sessions",
        "is_featured", "is_active", "hide_on_dashboard_upgrade", "order",
    )
    list_editable = ("is_featured", "is_active", "hide_on_dashboard_upgrade", "order")
    list_filter = ("plan_type", "duration", "is_active", "is_featured", "hide_on_dashboard_upgrade")
    search_fields = ("name", "tagline", "description")
    prepopulated_fields = {"slug": ("name",)}
    # The order-count Commission tiers inline is retired: the commerce fee is set
    # only as price ranges (minimum value to maximum value). Existing tiers were
    # moved into price ranges by billing migration 0022.
    inlines = (PlanFeatureInline, CommerceFeeBandInline, ActivityPenaltyInline)
    fieldsets = (
        (None, {"fields": ("name", "slug", "plan_type", "tagline", "description")}),
        (
            "Free-plan features",
            {
                # Tagged so plan_free_features.js can show this fieldset only
                # while plan type is Free (it is meaningless for other types).
                "classes": ("ow-free-only",),
                "fields": ("included_features",),
                "description": (
                    "Which dashboard areas (Edit content, Media, Orders, …) a "
                    "FREE-plan owner may open. Tick to include; anything left "
                    "unticked is shown locked as an “Upgrade”. Applies only to "
                    "Free plans, so this section appears only when the plan "
                    "type above is Free."
                ),
            },
        ),
        (
            "Free-plan · Edit-content tabs",
            {
                # Same reveal-only-for-Free behaviour (plan_free_features.js).
                "classes": ("ow-free-only",),
                "fields": ("included_editor_tabs",),
                "description": (
                    "Which tabs of the store's Edit-content page (Brand, Home "
                    "sections, Core pages, Custom scripts, …) a FREE-plan owner "
                    "may edit. Tick to allow; anything left unticked is shown "
                    "locked as an “Upgrade” inside the editor. Applies only to "
                    "Free plans."
                ),
            },
        ),
        (
            "Recurring pricing (Paid plans)",
            {
                "fields": ("duration", "currency", "price_monthly"),
                "description": (
                    "Pick the billing period this plan covers and its per-month "
                    "price. The customer is charged that price × the months in the "
                    "period (Quarterly ×3, Half-yearly ×6, Yearly ×12). Create a "
                    "separate plan for each duration you want to offer  they show "
                    "on the pricing page under the matching toggle. Leave at zero "
                    "for Free, Custom and One-time plans."
                ),
            },
        ),
        (
            "One-time pricing (One-time plans)",
            {
                "fields": ("one_time_price",),
                "description": (
                    "For a One-time plan: the whole amount, charged once, for a "
                    "full year. The recurring price above is ignored."
                ),
            },
        ),
        (
            "Entitlements",
            {
                "fields": ("number_of_stores", "number_of_collaborations", "concurrent_login_sessions"),
                "description": (
                    "How many stores the owner may create, how many collaborating "
                    "users they may invite, and how many devices they may stay "
                    "signed in on at the same time for one account. When a further "
                    "device signs in beyond the session limit, the least-recently "
                    "active device is signed out  so the account is only ever "
                    "managed on the allowed number of devices at once. Set the "
                    "session limit to 0 for unlimited."
                ),
            },
        ),
        (
            "Email marketing",
            {
                # Revealed by plan_email_marketing.js when the plan grants email
                # marketing  always for paid/one-time/custom plans, and for a
                # free plan the moment its "Email marketing" feature is ticked.
                "classes": ("ow-email-marketing",),
                "fields": (
                    "em_max_recipients",
                    "em_monthly_limit",
                    "em_emails_per_minute",
                    "em_allow_ai",
                    "em_scheduling",
                ),
                "description": (
                    "Limits for the store dashboard's Email marketing area. Whether "
                    "the feature is available at all is the ordinary gate  always on "
                    "for paid plans, and for a free plan when its “Email marketing” "
                    "box is ticked under Free-plan features above. These fields only "
                    "size the limits once it is available. 0 means unlimited."
                ),
            },
        ),
        ("Presentation", {"fields": ("badge", "is_featured", "cta_label", "custom_cta_url", "order", "is_active")}),
        (
            "User dashboard",
            {
                "fields": ("hide_on_dashboard_upgrade",),
                "description": (
                    "Whether this plan is offered on the “Upgrade plan” page inside "
                    "the user dashboard. Ticked plans are left off that page; they "
                    "still appear on the public pricing page."
                ),
            },
        ),
    )


@admin.register(PlanFeature)
class PlanFeatureAdmin(_HiddenFromIndexAdmin):
    """Features are edited inline on the plan; registered only so their change/
    history URLs resolve, hidden from the admin index."""

    list_display = ("plan", "text", "included", "order")
    list_filter = ("plan", "included")
    search_fields = ("text", "plan__name")


@admin.register(CommissionTier)
class CommissionTierAdmin(_HiddenFromIndexAdmin):
    """Commission bands are edited inline on the plan; registered only so their
    change/history URLs resolve, hidden from the admin index."""

    list_display = ("plan", "min_orders", "max_orders", "per_order_fee", "order")
    list_filter = ("plan",)
    search_fields = ("plan__name",)


@admin.register(CommerceFeeBand)
class CommerceFeeBandAdmin(_HiddenFromIndexAdmin):
    """Selling-price fee bands are edited inline on the plan; registered only so
    their change/history URLs resolve, hidden from the admin index."""

    list_display = ("plan", "min_price", "max_price", "fee", "order")
    list_filter = ("plan",)
    search_fields = ("plan__name",)


@admin.register(ActivityPenalty)
class ActivityPenaltyAdmin(_HiddenFromIndexAdmin):
    """Activity windows are edited inline on the plan; registered only so their
    change/history URLs resolve, hidden from the admin index."""

    list_display = ("plan", "window_days", "threshold_orders", "reactivation_penalty", "order")
    list_filter = ("plan",)
    search_fields = ("plan__name",)


@admin.register(Discount)
class DiscountAdmin(LeanModelAdmin):
    list_display = (
        "code", "kind", "value", "is_active", "times_redeemed",
        "max_redemptions", "starts_at", "ends_at",
    )
    list_editable = ("is_active",)
    list_filter = ("kind", "is_active")
    search_fields = ("code", "description")
    filter_horizontal = ("plans",)
    readonly_fields = ("times_redeemed",)
    fieldsets = (
        (None, {"fields": ("code", "description", "kind", "value", "is_active")}),
        ("Limits", {"fields": ("min_amount", "max_discount", "plans", "starts_at", "ends_at", "max_redemptions", "times_redeemed")}),
    )


@admin.register(SubscriptionOrder)
class SubscriptionOrderAdmin(LeanModelAdmin):
    """Read-mostly ledger. Orders are created by the checkout flow, not by hand;
    every money and payment field is locked so the admin can inspect and, at
    most, correct a status  never rewrite what a customer was charged."""

    list_display = (
        "number", "plan_name", "cycle", "customer_email", "total_amount",
        "status", "created_at",
    )
    list_filter = ("status", "cycle", "plan_name")
    search_fields = ("number", "customer_email", "customer_phone", "cf_order_id")
    date_hierarchy = "created_at"
    readonly_fields = tuple(
        f.name for f in SubscriptionOrder._meta.fields if f.name != "status"
    )

    def has_add_permission(self, request):
        return False


@admin.register(UserAgreement)
class UserAgreementAdmin(LeanModelAdmin):
    """The terms shown in the checkout acceptance modal. The most recently
    created active agreement is the one served; author a new one to supersede.

    Publishing a new version (e.g. "2") also asks every owner who bought a plan
    under an earlier version to accept it: the dashboard shows them an
    un-dismissable "We have updated our user agreement" popup, and their
    OTP-confirmed acceptance is logged in Agreement acceptances beside the
    earlier one (see billing.agreement_update).

    ``content`` is edited with the same free (GPL) TinyMCE rich-text editor as
    the marketing core pages and blog  full toolbar, images embedded inline as
    data URIs, assets served same-origin so it works unchanged in production."""

    form = _rich_form(UserAgreement)
    list_display = ("title", "version", "is_active", "acceptance_count", "created_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("title", "version")
    fields = ("title", "version", "content", "is_active")

    def get_queryset(self, request):
        # The body is a wide TEXT column; never sort it (MySQL error 1038).
        return super().get_queryset(request).defer("content")

    @admin.display(description="Accepted by")
    def acceptance_count(self, obj):
        count = AgreementAcceptance.objects.filter(agreement=obj).count()
        url = reverse("admin:billing_agreementacceptance_changelist") + "?" + urlencode({"agreement__id__exact": obj.pk})
        return format_html('<a href="{}">{} acceptance{}</a>', url, count, "" if count == 1 else "s")


@admin.register(AgreementAcceptance)
class AgreementAcceptanceAdmin(LeanModelAdmin):
    """Append-only compliance record of who accepted which agreement version,
    with the date and time. A new version's acceptance is a new row; earlier
    versions' rows are never removed, so one owner shows "v1 accepted" and
    "v2 accepted" side by side. Written by the checkout OTP step, the renewal
    checkout and the dashboard's updated-agreement popup; never edited by hand."""

    list_display = (
        "email", "user", "version_accepted", "accepted_date", "accepted_time",
        "source", "otp_verified", "ip_address",
    )
    list_filter = ("agreement", "source", "otp_verified")
    search_fields = ("email", "ip_address", "agreement_version", "agreement_title")
    date_hierarchy = "created_at"
    # LeanModelAdmin defers the joined rows' heavy columns (the agreement body).
    list_select_related = ("user", "agreement")
    readonly_fields = tuple(f.name for f in AgreementAcceptance._meta.fields)

    @admin.display(description="Agreement version", ordering="agreement_version")
    def version_accepted(self, obj):
        version = obj.agreement_version or (obj.agreement.version_label if obj.agreement else "")
        title = obj.agreement_title or (obj.agreement.title if obj.agreement else "")
        if version and title:
            return f"{title} · {version}"
        return version or title or "Built-in default"

    @admin.display(description="Date accepted", ordering="created_at")
    def accepted_date(self, obj):
        return timezone.localtime(obj.created_at).strftime("%d %b %Y") if obj.created_at else ""

    @admin.display(description="Time accepted")
    def accepted_time(self, obj):
        return timezone.localtime(obj.created_at).strftime("%H:%M:%S") if obj.created_at else ""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # A compliance record: keep every acceptance, old versions included.
        return False


class WebsiteTemplateForm(forms.ModelForm):
    class Meta:
        model = WebsiteTemplate
        fields = (
            "name", "description", "thumbnail", "template_type", "pricing", "price",
            "license_type", "is_available", "order",
        )

    def clean(self):
        data = super().clean()
        if data.get("pricing") == WebsiteTemplate.Pricing.PRICED and not (data.get("price") or 0) > 0:
            self.add_error("price", "A priced template needs a price above zero.")
        return data


@admin.register(WebsiteTemplate)
class WebsiteTemplateAdmin(LeanModelAdmin):
    """Storefront designs owners can pick (Settings → Storefront design, and the
    create-store form). One row per design deployed in code; new designs appear
    here automatically once deployed (billing.website_templates.REGISTRY) and
    cannot be added or deleted by hand.

    Set each design's name, thumbnail and store type, and whether it is free or
    priced. A priced design is bought through OneWooz's own Cashfree account
    (plus GST); its licence is either fixed (permanent, bound to the buyer's
    email) or yearly (expires a year after purchase and must be bought again).
    Stores already on a design keep it when you price it later."""

    form = WebsiteTemplateForm
    list_display = (
        "thumb", "name", "key", "template_type", "pricing", "price_label",
        "license_type", "is_available", "buyers", "order",
    )
    list_display_links = ("thumb", "name")
    list_editable = ("is_available", "order")
    list_filter = ("pricing", "license_type", "template_type", "is_available")
    search_fields = ("name", "key")
    readonly_fields = ("key", "thumb_large")
    fieldsets = (
        (None, {"fields": ("key", "name", "description", "thumbnail", "thumb_large", "template_type", "is_available", "order")}),
        ("Pricing", {
            "fields": ("pricing", "price", "license_type"),
            "description": "Free designs can be used by every store. Priced designs are charged "
                           "(plus GST) through the platform Cashfree account before they can be used.",
        }),
    )

    def get_queryset(self, request):
        from billing import website_templates

        website_templates.sync()  # newly deployed designs appear on their own
        return super().get_queryset(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Read-only since the marketplace: designs are priced and published in
        # Marketplace -> App items (OneWooz Templates). Kept for history.
        return False

    def changelist_view(self, request, extra_context=None):
        self.message_user(
            request,
            "Designs are now managed in Marketplace > App items (OneWooz Templates). This list is read-only history.",
            level=messages.WARNING,
        )
        return super().changelist_view(request, extra_context)

    @admin.display(description="Thumbnail")
    def thumb(self, obj):
        if obj.thumbnail:
            try:
                return format_html('<img src="{}" alt="" style="width:72px;height:54px;object-fit:cover;border:1px solid #ddd;">', obj.thumbnail.url)
            except Exception:
                return ""
        return ""

    @admin.display(description="Thumbnail preview")
    def thumb_large(self, obj):
        if obj and obj.thumbnail:
            try:
                return format_html('<img src="{}" alt="" style="max-width:360px;border:1px solid #ddd;">', obj.thumbnail.url)
            except Exception:
                return ""
        return "No thumbnail yet."

    @admin.display(description="Price")
    def price_label(self, obj):
        if not obj.is_priced:
            return "Free"
        suffix = " / year" if obj.is_yearly else " one-time"
        return f"\u20b9{obj.price:,.2f}{suffix}"

    @admin.display(description="Buyers")
    def buyers(self, obj):
        count = TemplatePurchase.objects.filter(
            template_key=obj.key, status=TemplatePurchase.Status.PAID
        ).count()
        url = reverse("admin:billing_templatepurchase_changelist") + "?" + urlencode(
            {"template_key": obj.key, "status__exact": TemplatePurchase.Status.PAID}
        )
        return format_html('<a href="{}">{}</a>', url, count)


@admin.register(TemplatePurchase)
class TemplatePurchaseAdmin(LeanModelAdmin):
    """Ledger of website template purchases. Created by the store Settings
    checkout and confirmed only by fetching Cashfree; money fields are locked.
    A permanent licence has no expiry; a yearly one shows when it ends."""

    list_display = (
        "number", "template_name", "email", "license_type", "total_amount",
        "status", "paid_at", "expires_at", "licence_state",
    )
    list_filter = ("status", "license_type", "template_key")
    search_fields = ("number", "email", "cf_order_id", "template_name")
    date_hierarchy = "created_at"
    readonly_fields = tuple(
        f.name for f in TemplatePurchase._meta.fields if f.name not in ("status", "expires_at")
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description="Licence")
    def licence_state(self, obj):
        if not obj.is_paid:
            return "-"
        if obj.expires_at is None:
            return "Permanent"
        return "Active" if obj.is_current else "Expired"


class SubscriptionInviteForm(forms.ModelForm):
    """Add form for a custom-priced invite: pick who, which plan, and the price."""

    class Meta:
        model = SubscriptionInvite
        fields = ("email", "plan", "custom_amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "plan" in self.fields:
            # Only plans that go through checkout (paid or one-time) can be granted.
            self.fields["plan"].queryset = SubscriptionPlan.objects.filter(
                is_active=True,
                plan_type__in=[SubscriptionPlan.Kind.PAID, SubscriptionPlan.Kind.ONE_TIME],
            ).order_by("order", "name")


@admin.register(SubscriptionInvite)
class SubscriptionInviteAdmin(LeanModelAdmin):
    """Issue a custom-priced purchase link for one email.

    Adding one mints a temporary link (valid ~20 minutes) to the same
    subscription checkout on the marketing site, charged at the price entered
    here (before GST)  or free, if that price is 0. After creation every field
    is locked and the shareable link is shown to copy and send.
    """

    form = SubscriptionInviteForm
    list_display = ("email", "plan", "custom_amount", "status", "created_at", "expires_at")
    list_filter = ("status", "plan")
    search_fields = ("email",)
    date_hierarchy = "created_at"

    _LOCKED = (
        "email", "plan", "custom_amount", "status", "order",
        "created_by", "expires_at", "completed_at", "invite_link_display",
    )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("email", "plan", "custom_amount")
        return self._LOCKED

    def get_readonly_fields(self, request, obj=None):
        # A created invite is view-only  its link is already out in the world.
        return () if obj is None else self._LOCKED

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = (getattr(request.user, "email", "") or str(request.user))[:254]
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        # Land on the change page (where the shareable link is shown), email the
        # link to the invited person, and put the environment-correct link in a
        # message so it can also be copied and sent by hand.
        from django.contrib import messages
        from django.http import HttpResponseRedirect
        from django.urls import reverse as dj_reverse

        from billing import emails
        from billing.cycles import DURATION_LABELS
        from billing.views import _invite_quote, invite_link

        link = invite_link(obj, request)

        q = _invite_quote(obj)
        is_free = q.total <= 0
        cycle_label = (
            "One-time · 1 year" if obj.plan.is_one_time
            else DURATION_LABELS.get(obj.plan.duration, obj.plan.get_duration_display())
        )
        amount_label = "Free" if is_free else f"₹{q.total:.2f}"

        sent = emails.send_subscription_invite(
            obj.email,
            link=link,
            plan_name=obj.plan.name,
            cycle_label=cycle_label,
            amount_label=amount_label,
            is_free=is_free,
            minutes=INVITE_TTL_MINUTES,
        )

        if sent:
            self.message_user(
                request,
                f"Invite created and emailed to {obj.email} "
                f"(valid ~{INVITE_TTL_MINUTES} min). You can also copy this link: {link}",
            )
        else:
            self.message_user(
                request,
                f"Invite created, but the email to {obj.email} could not be sent. "
                f"Copy and send this link manually (valid ~{INVITE_TTL_MINUTES} min): {link}",
                level=messages.WARNING,
            )
        return HttpResponseRedirect(
            dj_reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
        )

    @admin.display(description="Shareable link")
    def invite_link_display(self, obj):
        from django.utils.html import format_html

        from billing.views import invite_link

        if obj is None or obj.pk is None:
            return ""
        if not obj.is_usable:
            return "This link has expired or was already used."
        link = invite_link(obj)
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', link, link)


@admin.register(Notification)
class NotificationAdmin(LeanModelAdmin):
    """Author a notification that lands in an owner's top-navbar bell.

    Fill the recipient, a headline and a description; optionally paste an
    attachment link (it becomes a “View attachment” button that opens in a new
    tab). Automatic notifications (orders, low stock, shipment updates) also
    appear here read-only-ish, tagged by their kind.
    """

    list_display = ("headline", "user", "kind", "level", "is_read", "created_at")
    list_filter = ("kind", "level", "is_read", "created_at")
    search_fields = ("headline", "description", "user__email", "site_name")
    raw_id_fields = ("user",)
    readonly_fields = ("read_at", "created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("user", "headline", "description"),
            "description": (
                "The recipient owner, the bold title, and the body text of the "
                "notification. It appears in their dashboard bell immediately."
            ),
        }),
        ("Attachment & link", {
            "fields": ("attachment_url", "action_url"),
            "description": (
                "Attachment: a full URL (https://…)  shown as a “View "
                "attachment” button that opens in a new tab. Action link: an "
                "optional in-app path the headline points to."
            ),
        }),
        ("Classification", {
            "fields": ("kind", "level", "site_id_ref", "site_name"),
        }),
        ("State", {
            "fields": ("is_read", "read_at", "created_at", "updated_at"),
        }),
    )


# BROWSER PUSH: DISABLED (commented out, not removed). Uncomment this class and
# the PushSubscription import above to list subscribed browsers again.
#
# @admin.register(PushSubscription)
# class PushSubscriptionAdmin(LeanModelAdmin):
#     """Browsers that receive an owner's notifications as system pop-ups.
#
#     Created only by the owner clicking "Allow" in their browser, so there is
#     nothing to add here. Deleting a row stops pop-ups on that browser until the
#     owner next opens the dashboard there (it re-subscribes silently while the
#     browser still allows OneWooz).
#     """
#
#     list_display = ("user", "device_label", "created_at", "last_success_at", "failure_count")
#     search_fields = ("user__email", "device_label")
#     readonly_fields = (
#         "user", "device_label", "user_agent", "endpoint", "session_expires_at",
#         "last_success_at", "last_failure_at", "failure_count", "created_at", "updated_at",
#     )
#     exclude = ("p256dh", "auth")
#
#     def has_add_permission(self, request):
#         return False
#
#     def has_change_permission(self, request, obj=None):
#         return False


# ---------------------------------------------------------------------------
# Platform payment accounts  where owners pay their commission bills
# ---------------------------------------------------------------------------
@admin.register(PlatformPaymentAccount)
class PlatformPaymentAccountAdmin(LeanModelAdmin):
    """Create the bank-account and QR/UPI details shown to store owners on the
    bills page. Pick a kind, then fill the matching section  the other
    section's fields are simply left blank.

    * **Bank account**  bank name, IFSC code, account number (and, optionally,
      the account holder's name).
    * **QR code / UPI**  a UPI ID and an uploaded QR image.
    """

    list_display = ("display_name", "kind", "is_active", "created_at")
    list_editable = ("is_active",)
    list_filter = ("kind", "is_active")
    search_fields = ("label", "bank_name", "account_number", "ifsc_code", "upi_id")
    readonly_fields = ("qr_preview", "created_at", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("kind", "label", "is_active", "note"),
            "description": (
                "Choose the kind of payment account, then fill the matching "
                "section below. Only active accounts are shown to owners."
            ),
        }),
        ("Bank account", {
            "fields": ("bank_name", "account_holder", "ifsc_code", "account_number"),
            "description": "Fill these for a bank-account object.",
        }),
        ("QR code / UPI", {
            "fields": ("upi_id", "qr_image", "qr_preview"),
            "description": "Fill these for a QR / UPI object.",
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="QR preview")
    def qr_preview(self, obj):
        from django.utils.html import format_html

        if obj and obj.qr_image:
            return format_html(
                '<img src="{}" alt="QR code" style="max-width:220px;max-height:220px;'
                'border:1px solid #ddd;padding:4px;background:#fff;">',
                obj.qr_image.url,
            )
        return "No QR image uploaded."


class _TermsWidget(forms.Textarea):
    """Terms & Conditions as numbered points: the value rides in a hidden
    textarea as a JSON list, and js/admin/invoice_details.js builds the
    add / remove / reorder point editor on top of it."""

    def __init__(self, attrs=None):
        super().__init__(attrs={"data-terms-editor": "1", "rows": 4, **(attrs or {})})

    def format_value(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = [value] if value.strip() else []
        return json.dumps([str(v) for v in (value or [])], ensure_ascii=False)


class InvoiceDetailsForm(forms.ModelForm):
    state = forms.ChoiceField(
        label="State (place of supply)",
        required=False,
        help_text=(
            "Detected automatically from the GSTIN (its first two digits). A buyer "
            "in this state is billed CGST 9% + SGST 9%; any other state IGST 18%."
        ),
    )
    terms = forms.CharField(label="Terms & Conditions", required=False, widget=_TermsWidget)

    class Meta:
        model = InvoiceDetails
        fields = (
            "label", "company_name", "mobile_number", "email", "address", "gstin",
            "hsn_code", "state", "terms", "signature", "designation", "is_active",
        )

    class Media:
        js = ("js/admin/invoice_details.js",)
        css = {"all": ("css/admin/invoice_details.css",)}

    def __init__(self, *args, **kwargs):
        from django.conf import settings as dj_settings

        from billing import gst

        super().__init__(*args, **kwargs)
        self.fields["state"].choices = [("", "- Detect from GSTIN -")] + [(s, s) for s in gst.STATES]
        # GSTIN state codes for the live auto-detect in the admin JS.
        self.fields["gstin"].widget.attrs.update({
            "data-gst-states": json.dumps(gst.STATE_BY_CODE),
            "autocomplete": "off",
            "style": "text-transform:uppercase",
        })
        # A new object starts with the default terms so they can be edited.
        if not self.instance.pk and not self.initial.get("terms"):
            base = getattr(dj_settings, "PLATFORM_BILLING_ENTITY", {}) or {}
            self.initial["terms"] = list(base.get("terms") or [])

    def clean_gstin(self):
        from billing import gst

        value = gst.normalise_gstin(self.cleaned_data.get("gstin"))
        if value and not gst.is_valid_gstin(value):
            raise ValidationError("That doesn’t look like a valid 15-character GSTIN.")
        return value

    def clean_terms(self):
        raw = self.cleaned_data.get("terms") or "[]"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            # Plain text fallback (JS disabled): one point per line.
            data = [line for line in str(raw).splitlines()]
        if not isinstance(data, list):
            raise ValidationError("Terms must be a list of points.")
        return [" ".join(str(t).split()) for t in data if str(t).strip()]

    def clean(self):
        from billing import gst

        cleaned = super().clean()
        detected = gst.state_from_gstin(cleaned.get("gstin"))
        if detected:
            cleaned["state"] = detected  # the GSTIN's state always wins
        return cleaned


class CommerceBillDetailsForm(forms.ModelForm):
    state = forms.ChoiceField(
        label="State",
        required=False,
        help_text=(
            "Detected automatically from the GSTIN. A store in this state pays "
            "CGST 9% + SGST 9% on the commerce fee; any other state IGST 18%."
        ),
    )

    class Meta:
        model = CommerceBillDetails
        fields = (
            "label", "is_active", "company_name", "address", "gstin", "pan", "state",
            "sac_code", "email", "phone", "bank_name", "account_holder",
            "account_number", "ifsc_code", "branch", "upi_id", "payment_note",
        )

    def __init__(self, *args, **kwargs):
        from billing import gst

        super().__init__(*args, **kwargs)
        self.fields["state"].choices = [("", "- Detect from GSTIN -")] + [(s, s) for s in gst.STATES]
        self.fields["gstin"].widget.attrs.update({"autocomplete": "off", "style": "text-transform:uppercase"})

    def clean_gstin(self):
        from billing import gst

        value = gst.normalise_gstin(self.cleaned_data.get("gstin"))
        if value and not gst.is_valid_gstin(value):
            raise ValidationError("That doesn’t look like a valid 15-character GSTIN.")
        return value

    def clean(self):
        from billing import gst

        cleaned = super().clean()
        detected = gst.state_from_gstin(cleaned.get("gstin"))
        if detected:
            cleaned["state"] = detected
        return cleaned


@admin.register(CommerceBillDetails)
class CommerceBillDetailsAdmin(LeanModelAdmin):
    """The billing company and payment details printed on every store's
    commerce fee bill (Bills page and its PDF). The newest *active* object is
    used. Blank company fields fall back to "Invoice details"; with no bank
    account / UPI here, the active Platform payment accounts are printed."""

    form = CommerceBillDetailsForm
    list_display = ("__str__", "company_name", "gstin", "state", "account_number", "upi_id", "is_active", "in_use", "created_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("label", "company_name", "gstin", "account_number", "upi_id")
    readonly_fields = ("tax_rule", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("label", "is_active")}),
        ("Billing company", {"fields": ("company_name", "address", "email", "phone")}),
        ("Tax", {"fields": ("gstin", "pan", "state", "tax_rule", "sac_code")}),
        ("Payment details", {
            "fields": ("bank_name", "account_holder", "account_number", "ifsc_code", "branch", "upi_id", "payment_note"),
            "description": "Printed on every bill so the store knows where to pay.",
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="GST on the commerce fee")
    def tax_rule(self, obj):
        state = getattr(obj, "state", "") if obj else ""
        if not state:
            return "Save with a GSTIN (or a state) to fix the place of supply."
        return format_html(
            "Stores in <strong>{}</strong>: CGST 9% + SGST 9%. Stores in any other state: IGST 18%.",
            state,
        )

    @admin.display(description="Printed on bills", boolean=True)
    def in_use(self, obj):
        current = CommerceBillDetails.current()
        return bool(current and obj and current.pk == obj.pk)


@admin.register(PricingFeature)
class PricingFeatureAdmin(LeanModelAdmin):
    """The feature tabs shown under the pricing cards on the pricing page:
    each object is a tab title and the description revealed when it is
    clicked. Reorder with the Order column; untick Active to hide one."""

    list_display = ("title", "short_description", "order", "is_active", "updated_at")
    list_editable = ("order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "description")
    ordering = ("order", "created_at")
    fields = ("title", "description", "order", "is_active")

    @admin.display(description="Description")
    def short_description(self, obj):
        text = " ".join((obj.description or "").split())
        return text if len(text) <= 90 else text[:87] + "..."


@admin.register(InvoiceDetails)
class InvoiceDetailsAdmin(LeanModelAdmin):
    """The seller details on owners' subscription invoices (Profile →
    Subscription → View bill): company, mobile, email, address, GSTIN, HSN,
    state (detected from the GSTIN; it decides CGST + SGST vs IGST), the Terms
    & Conditions points and the signatory's signature. The newest *active*
    object is used; blank fields fall back to the defaults."""

    form = InvoiceDetailsForm
    list_display = ("__str__", "gstin", "state", "signature_thumb", "is_active", "in_use", "created_at")
    list_editable = ("is_active",)
    list_filter = ("is_active", "state")
    search_fields = ("label", "company_name", "gstin", "email", "mobile_number")
    readonly_fields = ("tax_rule", "signature_preview", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("label", "is_active")}),
        ("Company", {"fields": ("company_name", "mobile_number", "email", "address")}),
        ("Tax", {"fields": ("gstin", "state", "tax_rule", "hsn_code")}),
        ("Terms & Conditions", {
            "fields": ("terms",),
            "description": "Each point is printed numbered on the invoice, in this order.",
        }),
        ("Authorised signatory", {"fields": ("signature", "signature_preview", "designation")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="GST on invoices")
    def tax_rule(self, obj):
        state = getattr(obj, "state", "") if obj else ""
        if not state:
            return "Save with a GSTIN (or a state) to fix the place of supply."
        return format_html(
            "Buyers in <strong>{}</strong>: CGST 9% + SGST 9%. Buyers in any other state: IGST 18%.",
            state,
        )

    @admin.display(description="Signature")
    def signature_thumb(self, obj):
        if obj and obj.signature:
            return format_html(
                '<img src="{}" alt="Signature" style="height:32px;width:auto;background:#fff;">',
                obj.signature.url,
            )
        return "-"

    @admin.display(description="Preview")
    def signature_preview(self, obj):
        if obj and obj.signature:
            return format_html(
                '<img src="{}" alt="Signature" style="height:56px;width:auto;transform:rotate(5deg);'
                'border:1px solid #ddd;padding:6px;background:#fff;">',
                obj.signature.url,
            )
        return "Upload an image to preview it as it appears on the invoice."

    @admin.display(description="Used on bills", boolean=True)
    def in_use(self, obj):
        current = InvoiceDetails.current()
        return bool(current and obj and current.pk == obj.pk)


# ---------------------------------------------------------------------------
# Commission bills  inspect and confirm owner payment proofs
# ---------------------------------------------------------------------------
@admin.register(GoogleMerchantConfig)
class GoogleMerchantConfigAdmin(TenantSafeModelAdmin):
    """Stores' Google Merchant connections (read-only: owners connect from their
    dashboard with Google sign-in).

    **One-time platform setup:** connect OneWooz's *own* Merchant Center account
    from any store (with a Google account that is ADMIN there), then select that
    row and run “Register OneWooz's Cloud project with this Merchant Center
    account”. Google requires this developer registration once per Cloud project,
    on the platform's own primary account, never on each merchant's."""

    list_display = (
        "site", "status", "merchant_id", "account_name", "google_email",
        "website_claimed", "auto_sync", "sync_state", "last_synced_at",
    )
    list_filter = ("status", "website_claimed", "auto_sync", "sync_state")
    search_fields = ("site__name", "merchant_id", "account_name", "google_email")
    list_select_related = ("site",)
    exclude = ("refresh_token", "access_token", "service_account_json")
    actions = ("register_platform_project", "queue_full_sync")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields if f.name not in self.exclude]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Register OneWooz's Cloud project with this Merchant Center account (one-time platform setup)")
    def register_platform_project(self, request, queryset):
        from commerce import google_merchant as gm

        if queryset.count() != 1:
            self.message_user(request, "Select exactly one connection: OneWooz's own Merchant Center account.", messages.ERROR)
            return
        cfg = queryset.select_related("site").first()
        if not (cfg.refresh_token and cfg.merchant_id):
            self.message_user(
                request,
                "Sign in with Google on that store and enter OneWooz's Merchant Center ID first.",
                messages.ERROR,
            )
            return
        try:
            result = gm.register_platform_project(cfg)
        except gm.MerchantError as exc:
            detail = exc.reason or exc.message
            if "ALREADY_REGISTERED" in detail.upper():
                self.message_user(request, "The Cloud project is already registered with a Merchant Center account.", messages.WARNING)
            else:
                self.message_user(request, f"Registration failed: {exc.message} ({detail})", messages.ERROR)
            return
        ids = ", ".join(result.get("gcpIds") or []) or "the Cloud project"
        self.message_user(request, f"Registered {ids} with Merchant Center account {cfg.merchant_id}.", messages.SUCCESS)

    @admin.action(description="Run a full product sync now")
    def queue_full_sync(self, request, queryset):
        from commerce.tasks import google_merchant_full_sync

        n = 0
        for cfg in queryset:
            if cfg.is_connected:
                google_merchant_full_sync.delay(str(cfg.site_id), False)
                n += 1
        self.message_user(request, f"Queued {n} sync(s).", messages.SUCCESS)


@admin.register(GoogleMerchantProductStatus)
class GoogleMerchantProductStatusAdmin(TenantSafeModelAdmin):
    list_display = ("product", "site", "state", "last_synced_at")
    list_filter = ("state",)
    search_fields = ("product__title", "site__name")
    list_select_related = ("product", "site")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(BillingConfig)
class BillingConfigAdmin(TenantSafeModelAdmin):
    """A store's billing settings. The per-order charge here is a FALLBACK
    only: the commerce fee is charged by selling price from the owner's plan
    ("Commerce fee by selling price" on the subscription plan). This flat
    charge applies only to a store whose plan has no bands (and no order-count
    Commission tiers); a percentage is then taken of the selling value before
    coupons, never of the discounted order total."""

    list_display = ("site", "fee_basis", "per_order_charge_type", "per_order_charge_value", "billing_cycle_type", "enabled")
    list_filter = ("enabled", "per_order_charge_type", "billing_cycle_type")
    search_fields = ("site__name",)
    list_select_related = ("site",)
    readonly_fields = ("fee_basis", "order_charge_event", "last_bill_generated_at")
    fields = (
        "site", "enabled", "fee_basis", "per_order_charge_type", "per_order_charge_value",
        "order_charge_event", "billing_cycle_type", "billing_cycle_days",
        "current_period_start", "last_bill_generated_at",
    )

    @admin.display(description="Commerce fee basis")
    def fee_basis(self, obj):
        if obj is None or not obj.site_id:
            return "-"
        from commerce import billing_service

        info = billing_service.current_commission(obj.site)
        if info.get("type") == "bands":
            return "Selling-price bands from the plan (the flat charge below is not used)"
        if info.get("type") == "fixed" and info.get("value") is not None and info.get("value") != obj.per_order_charge_value:
            return f"Plan commission tier: ₹{info['value']} per order"
        return "Flat fallback below (add Commerce fee bands to the plan to charge by selling price)"


@admin.register(Bill)
class BillAdmin(TenantSafeModelAdmin):
    """Store commission bills. An operator's job here is to review a submitted
    payment screenshot and, once satisfied, mark the bill paid  which is what
    the ``Mark selected bills as paid`` action does."""

    list_display = (
        "invoice_number", "site", "total_amount", "total_orders",
        "status", "screenshot_status", "period_end",
    )
    list_filter = ("status", "screenshot_status")
    search_fields = ("invoice_number", "site__name")
    date_hierarchy = "period_end"
    readonly_fields = (
        "site", "period_start", "period_end", "currency", "total_amount",
        "total_orders", "invoice_number", "invoice_pdf", "payment_reference_display",
        "screenshot_preview", "paid_at", "created_at", "updated_at",
    )
    fields = (
        "site", "invoice_number", "period_start", "period_end", "currency",
        "total_amount", "total_orders", "status", "screenshot_status",
        "payment_reference_display", "payment_screenshot", "screenshot_preview",
        "paid_at", "created_at", "updated_at",
    )
    actions = ("mark_paid",)

    def has_add_permission(self, request):
        # Bills are generated by the store owner from their dashboard, not by hand.
        return False

    @admin.display(description="Payment reference")
    def payment_reference_display(self, obj):
        if obj is None:
            return ""
        return (obj.meta or {}).get("payment_reference") or ""

    @admin.display(description="Payment screenshot")
    def screenshot_preview(self, obj):
        from django.utils.html import format_html

        if obj and obj.payment_screenshot:
            return format_html(
                '<a href="{0}" target="_blank" rel="noopener">'
                '<img src="{0}" alt="Payment proof" style="max-width:320px;'
                'max-height:400px;border:1px solid #ddd;padding:4px;background:#fff;"></a>',
                obj.payment_screenshot.url,
            )
        return "No screenshot submitted yet."

    @admin.action(description="Mark selected bills as paid")
    def mark_paid(self, request, queryset):
        from django.utils import timezone

        updated = 0
        for bill in queryset:
            bill.status = Bill.Status.PAID
            bill.screenshot_status = Bill.ScreenshotStatus.APPROVED
            if bill.paid_at is None:
                bill.paid_at = timezone.now()
            bill.save(update_fields=["status", "screenshot_status", "paid_at", "updated_at"])
            updated += 1
        self.message_user(request, f"{updated} bill(s) marked as paid.")


# ---------------------------------------------------------------------------
# Store customers (shoppers), including accounts the shopper deleted
# ---------------------------------------------------------------------------
class _CustomerStatusFilter(admin.SimpleListFilter):
    title = "account status"
    parameter_name = "account"

    def lookups(self, request, model_admin):
        return (
            ("active", "Active"),
            ("deleted_by_customer", "Deleted by customer"),
            ("removed_by_owner", "Removed by store owner"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "active":
            return queryset.filter(is_deleted=False)
        if value == "deleted_by_customer":
            return queryset.filter(account_deleted_at__isnull=False)
        if value == "removed_by_owner":
            return queryset.filter(is_deleted=True, account_deleted_at__isnull=True)
        return queryset


class _CustomerAddressInline(admin.TabularInline):
    """Saved addresses (kept for deleted accounts too). Read-only here."""

    model = CustomerAddress
    extra = 0
    can_delete = False
    fields = ("label", "address_type", "house_no", "street_area", "city", "state", "pincode", "phone", "is_default")
    readonly_fields = fields
    show_change_link = False

    def get_queryset(self, request):
        # Tenant-scoped model: read through the unscoped manager (the parent
        # customer already narrows it to one store).
        return CustomerAddress.all_objects.all()

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(TenantSafeModelAdmin):
    """Every store's shoppers. A shopper who deleted their own account is kept
    (nothing is erased): the store owner only sees their name with an "Account
    deleted" badge, while here their original email and phone stay visible."""

    list_display = ("display_name", "contact_email", "contact_phone", "site", "account_status", "created_at")
    list_filter = (_CustomerStatusFilter,)
    search_fields = ("name", "email", "deleted_email", "phone", "deleted_phone", "site__name")
    list_select_related = ("site",)
    readonly_fields = ("account_deleted_at", "deleted_email", "deleted_phone", "is_deleted", "deleted_at", "created_at", "updated_at")
    fieldsets = (
        ("Customer", {"fields": ("site", "name", "email", "phone")}),
        ("Account deletion", {
            "fields": ("account_deleted_at", "deleted_email", "deleted_phone", "is_deleted", "deleted_at"),
            "description": "Filled when the shopper deleted their own account from the store. "
                           "Their data is kept here; the store no longer shows or uses it.",
        }),
        ("Record", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (_CustomerAddressInline,)

    @admin.display(description="Name", ordering="name")
    def display_name(self, obj):
        return obj.name or "(no name)"

    @admin.display(description="Email")
    def contact_email(self, obj):
        return (obj.deleted_email or obj.email) if obj.account_deleted_at else obj.email

    @admin.display(description="Phone")
    def contact_phone(self, obj):
        return (obj.deleted_phone or obj.phone) if obj.account_deleted_at else obj.phone

    @admin.display(description="Status")
    def account_status(self, obj):
        if obj.account_deleted_at:
            return format_html(
                '<span style="color:#b91c1c;font-weight:600">Deleted by customer</span> '
                '<span style="color:#6b7280">({})</span>',
                timezone.localtime(obj.account_deleted_at).strftime("%d %b %Y"),
            )
        if obj.is_deleted:
            return "Removed by store owner"
        return "Active"


# ---------------------------------------------------------------------------
# Product form tooltips  the "!" hover help beside each product-form field
# ---------------------------------------------------------------------------
class ProductFieldTooltipForm(forms.ModelForm):
    """The field key is picked from the product form's real inputs (never typed),
    and the description is a roomy textarea."""

    field_key = forms.ChoiceField(label="Product form field", choices=())

    class Meta:
        model = ProductFieldTooltip
        fields = ("field_key", "description", "is_active", "order")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "cols": 80, "maxlength": 600}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from commerce import field_tips

        self.fields["field_key"].choices = field_tips.choices()

    def save(self, commit=True):
        from commerce import field_tips

        obj = super().save(commit=False)
        obj.label = field_tips.label_for(obj.field_key)
        if commit:
            obj.save()
        return obj


@admin.register(ProductFieldTooltip)
class ProductFieldTooltipAdmin(LeanModelAdmin):
    """Hover help for the store owner's create / edit product form.

    Every product-form input is listed here automatically (one row each). Type a
    description for a field and the owner sees a small "!" circle just right of
    that field's label; hovering it shows the text. Leave a description blank
    (or untick Active) to show no icon for that field."""

    form = ProductFieldTooltipForm
    list_display = ("label", "field_key", "short_description", "is_active", "order")
    list_display_links = ("label",)
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("label", "field_key", "description")
    ordering = ("order", "field_key")
    list_per_page = 100

    def changelist_view(self, request, extra_context=None):
        # Seed a blank row for any product-form field that has none yet, so the
        # list always covers every input without anyone adding them by hand.
        from commerce import field_tips

        field_tips.ensure_rows()
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description="Tooltip")
    def short_description(self, obj):
        text = " ".join((obj.description or "").split())
        if not text:
            return format_html('<span style="color:#9ca3af">{}</span>', "No tooltip (icon hidden)")
        return text if len(text) <= 90 else text[:87] + "..."


# ---------------------------------------------------------------------------
# onewooz.com SEO  the marketing site's own "same / different per page" SEO
# ---------------------------------------------------------------------------
_SEO_TEXTAREA = forms.Textarea(attrs={"rows": 2, "cols": 80})
_SCHEMA_TEXTAREA = forms.Textarea(attrs={
    "rows": 12, "cols": 100, "spellcheck": "false",
    "style": "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; width: 100%; max-width: 60rem;",
    "placeholder": '<script type="application/ld+json">\n{\n  "@context": "https://schema.org",\n  "@type": "...",\n  ...\n}\n</script>',
})


class _SchemaMarkupMixin:
    """Validates pasted JSON-LD on save so a typo never reaches the site."""

    def clean_schema_markup(self):
        from web.seo_overrides import parse_schema

        text = (self.cleaned_data.get("schema_markup") or "").strip()
        try:
            parse_schema(text)
        except ValueError as exc:
            raise ValidationError(str(exc))
        return text


class MarketingPageSeoForm(_SchemaMarkupMixin, forms.ModelForm):
    """One page's SEO. The page is picked from every real onewooz.com page
    (fixed pages, legal policies, content pages, blog posts), never typed."""

    page_key = forms.ChoiceField(label="Page", choices=())

    class Meta:
        model = MarketingPageSeo
        fields = (
            "page_key",
            "meta_title", "keywords", "meta_description",
            "og_title", "og_description", "og_image",
            "twitter_title", "twitter_description", "twitter_image",
            "schema_markup", "replace_built_in_schema",
            "canonical_url", "noindex",
        )
        labels = {"schema_markup": "Schema (JSON-LD)"}
        widgets = {
            "meta_description": _SEO_TEXTAREA,
            "og_description": _SEO_TEXTAREA,
            "twitter_description": _SEO_TEXTAREA,
            "schema_markup": _SCHEMA_TEXTAREA,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from web.seo_overrides import page_choices

        current = self.instance.page_key if self.instance and self.instance.pk else ""
        self.fields["page_key"].choices = page_choices(current)


class _MarketingPageSeoFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        seen = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            key = form.cleaned_data.get("page_key")
            if not key:
                continue
            if key in seen:
                raise ValidationError("Each page can only be listed once. Remove the duplicate page row.")
            seen.add(key)


class MarketingPageSeoInline(admin.StackedInline):
    model = MarketingPageSeo
    form = MarketingPageSeoForm
    formset = _MarketingPageSeoFormSet
    extra = 0
    verbose_name = "page"
    verbose_name_plural = "Page SEO (one block per page; a blank field keeps that page's built-in value)"
    fieldsets = (
        (None, {"fields": ("page_key",)}),
        ("Search results", {"fields": ("meta_title", "keywords", "meta_description")}),
        ("Open Graph (Facebook, WhatsApp, LinkedIn)", {"fields": ("og_title", "og_description", "og_image")}),
        ("Twitter / X card", {"fields": ("twitter_title", "twitter_description", "twitter_image")}),
        ("Schema / structured data", {"fields": ("schema_markup", "replace_built_in_schema")}),
        ("Advanced", {"fields": ("canonical_url", "noindex")}),
    )


class MarketingSeoSettingsForm(_SchemaMarkupMixin, forms.ModelForm):
    class Meta:
        model = MarketingSeoSettings
        fields = (
            "site_logo", "favicon", "favicon_dark",
            "meta_title", "keywords", "meta_description",
            "og_title", "og_description", "og_image",
            "twitter_title", "twitter_description", "twitter_image",
            "schema_markup", "replace_built_in_schema",
        )
        labels = {
            "schema_markup": "Home page schema (JSON-LD)",
            "site_logo": "Logo",
            "favicon_dark": "Favicon (dark mode)",
        }
        help_texts = {
            "schema_markup": (
                "Structured data (schema.org JSON-LD) for the home page. Paste the JSON, or the "
                "whole script block(s) exactly as a schema generator gives them; several blocks "
                "are fine. It is checked on save. For any other page, use its Page SEO block "
                "below."
            ),
        }
        widgets = {
            "meta_description": _SEO_TEXTAREA,
            "og_description": _SEO_TEXTAREA,
            "twitter_description": _SEO_TEXTAREA,
            "schema_markup": _SCHEMA_TEXTAREA,
        }


@admin.register(MarketingPageSeo)
class MarketingPageSeoAdmin(_HiddenFromIndexAdmin):
    """Page rows are edited inside "onewooz.com SEO", never on their own list."""

    form = MarketingPageSeoForm


@admin.register(MarketingSeoSettings)
class MarketingSeoSettingsAdmin(LeanModelAdmin):
    """SEO for onewooz.com itself, always set per page.

    The top set is the home page's SEO and fills what another page lacks
    (keywords, share image). Every other page gets its own "Page SEO" block:
    pick the page, fill any field, and leave the rest blank to keep that page's
    built-in, keyword-tuned value. Changes apply on the next page load."""

    form = MarketingSeoSettingsForm
    inlines = (MarketingPageSeoInline,)
    fieldsets = (
        ("Brand: logo & favicon", {
            "fields": ("site_logo", "favicon", "favicon_dark"),
            "description": (
                "Replace the OneWooz logo and browser-tab icon everywhere they show "
                "(onewooz.com, sign-in pages, owner dashboard, this admin). Each upload "
                "is shown at the same size as the current one. Clear a field to go back "
                "to the built-in file."
            ),
        }),
        ("Site-wide & home page: search results", {
            "fields": ("meta_title", "keywords", "meta_description"),
            "description": (
                "Every page has its own SEO. This top set is the home page's and fills anything "
                "another page lacks (keywords, share image). Add a Page SEO block below for each "
                "other page you want to change; a blank field keeps that page's built-in value."
            ),
        }),
        ("Site-wide & home page: Open Graph", {"fields": ("og_title", "og_description", "og_image")}),
        ("Site-wide & home page: Twitter / X card", {"fields": ("twitter_title", "twitter_description", "twitter_image")}),
        ("Home page: schema / structured data", {"fields": ("schema_markup", "replace_built_in_schema")}),
    )

    def save_model(self, request, obj, form, change):
        # Per-page SEO is the only mode; keep the stored value consistent.
        obj.mode = MarketingSeoSettings.Mode.CUSTOM
        super().save_model(request, obj, form, change)

    # A single settings object: the list opens it directly, and it can be
    # neither added twice nor deleted.
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = MarketingSeoSettings.current()
        return redirect(
            reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
                current_app=self.admin_site.name,
            )
        )


# Everything else (models without a bespoke admin above) is auto-registered;
# is_registered() means these explicit registrations are left untouched.
# OneWooz Apps (marketplace): its own module, registered before the generic
# catch-all so its curated admins win.
from . import marketplace_admin  # noqa: E402,F401

_register_all()
