"""Generate the onewooz.com core pages: pages → subpages → child pages.

Builds a complete, presentable example tree (Help Centre, Selling Guides,
Company) from ``web.sample_content.core_pages``: real copy with headings,
lists, tables and working cross-links, never placeholder text.

Usage::

    python manage.py generate_core_pages               # create what is missing
    python manage.py generate_core_pages --dry-run     # preview, write nothing
    python manage.py generate_core_pages --update      # also refresh existing pages
    python manage.py generate_core_pages --only help-centre company
    python manage.py generate_core_pages --drafts      # new pages start unpublished

Idempotent: every node is matched by its slug (child pages within their
subpage, subpages within their page), so running it again never duplicates
anything. Without ``--update`` an existing page is left exactly as it is, so
edits made in the admin are safe. ``--update`` overwrites only title, content
and order; publish and footer switches set in the admin are kept.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from web.models import MarketingCoreChildPage, MarketingCorePage, MarketingCoreSubpage
from web.sample_content import clean
from web.sample_content.core_pages import CORE_PAGES

_MARK = {"created": "+", "updated": "~", "unchanged": "=", "kept": "="}


class Command(BaseCommand):
    help = (
        "Generate example core pages, subpages and child pages for onewooz.com "
        "(idempotent; matched by slug)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite the title, content and order of pages that already exist.",
        )
        parser.add_argument(
            "--only",
            nargs="+",
            metavar="SLUG",
            help="Generate only these top-level pages (by slug), with their subpages.",
        )
        parser.add_argument(
            "--drafts",
            action="store_true",
            help="Create new pages unpublished, to review in the admin before they go live.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created or updated, then roll everything back.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        self.update = options["update"]
        self.published = not options["drafts"]
        dry_run = options["dry_run"]

        pages = CORE_PAGES
        if options["only"]:
            wanted = set(options["only"])
            known = {p["slug"] for p in CORE_PAGES}
            unknown = sorted(wanted - known)
            if unknown:
                raise CommandError(
                    f"Unknown page slug(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(sorted(known))}."
                )
            pages = [p for p in CORE_PAGES if p["slug"] in wanted]

        self.counts = {
            level: {"created": 0, "updated": 0, "unchanged": 0, "kept": 0}
            for level in ("pages", "subpages", "children")
        }

        with transaction.atomic():
            for p_index, page_data in enumerate(pages, start=1):
                self._generate_page(p_index, page_data)
            if dry_run:
                transaction.set_rollback(True)

        if not dry_run:
            _invalidate_seo_cache()
        self._summary(dry_run)

    # ------------------------------------------------------------------
    def _generate_page(self, order: int, data: dict) -> None:
        page, state = _upsert(
            MarketingCorePage,
            lookup={"slug": data["slug"]},
            fields={"title": data["title"], "content": clean(data["content"]), "order": order},
            create_only={
                "is_published": self.published,
                "show_in_footer": data.get("show_in_footer", True),
            },
            update=self.update,
        )
        self._line(0, data["title"], page.get_absolute_url(), state, "pages")

        for s_index, sub_data in enumerate(data.get("subpages", []), start=1):
            sub, state = _upsert(
                MarketingCoreSubpage,
                lookup={"core_page": page, "slug": sub_data["slug"]},
                fields={
                    "title": sub_data["title"],
                    "content": clean(sub_data["content"]),
                    "order": s_index,
                },
                create_only={"is_published": self.published},
                update=self.update,
            )
            self._line(1, sub_data["title"], sub.get_absolute_url(), state, "subpages")

            for c_index, child_data in enumerate(sub_data.get("children", []), start=1):
                child, state = _upsert(
                    MarketingCoreChildPage,
                    lookup={"subpage": sub, "slug": child_data["slug"]},
                    fields={
                        "title": child_data["title"],
                        "content": clean(child_data["content"]),
                        "order": c_index,
                    },
                    create_only={"is_published": self.published},
                    update=self.update,
                )
                self._line(2, child_data["title"], child.get_absolute_url(), state, "children")

    # ------------------------------------------------------------------
    def _line(self, depth: int, title: str, url: str, state: str, level: str) -> None:
        self.counts[level][state] += 1
        style = {
            "created": self.style.SUCCESS,
            "updated": self.style.WARNING,
        }.get(state, lambda s: s)
        label = f"{'    ' * depth}{_MARK[state]} {title}"
        self.stdout.write(style(f"{label:<58} {url:<62} {state}"))

    def _summary(self, dry_run: bool) -> None:
        def part(level: str, name: str) -> str:
            c = self.counts[level]
            same = c["unchanged"] + c["kept"]
            return f"{name}: {c['created']} created, {c['updated']} updated, {same} unchanged"

        self.stdout.write("")
        self.stdout.write(
            "  ".join(
                [
                    part("pages", "Pages"),
                    part("subpages", "Subpages"),
                    part("children", "Child pages"),
                ]
            )
        )
        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry run: nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Core pages generated. Browse them at /pages/."))
        if not self.update and any(self.counts[l]["kept"] for l in self.counts):
            self.stdout.write("Existing pages were left untouched. Use --update to refresh their content.")


def _upsert(model, *, lookup: dict, fields: dict, create_only: dict, update: bool):
    """Create the row, or refresh it when ``update`` is set.

    Returns ``(obj, state)`` with state one of created / updated / unchanged /
    kept. Lookups are on unique keys, so ``get`` never sorts or scans.
    """
    try:
        obj = model.objects.get(**lookup)
    except model.DoesNotExist:
        obj = model(**lookup, **fields, **create_only)
        obj.save()
        return obj, "created"

    if not update:
        return obj, "kept"
    changed = [name for name, value in fields.items() if getattr(obj, name) != value]
    if not changed:
        return obj, "unchanged"
    for name in changed:
        setattr(obj, name, fields[name])
    obj.save()
    return obj, "updated"


def _invalidate_seo_cache() -> None:
    """New pages appear in the admin's per-page SEO catalogue straight away."""
    try:
        from web.seo_overrides import invalidate

        invalidate()
    except Exception:  # cache unavailable: entries expire on their own
        pass
