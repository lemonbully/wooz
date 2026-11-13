"""Seed the marketing site's core pages and a sample blog post.

Idempotent: keyed on slug, so running it repeatedly won't create duplicates. Run
once after migrating::

    python manage.py seed_marketing_content

Owners still edit everything afterwards in the admin (arcane…/db/).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from web.models import (
    MarketingBlog,
    MarketingCoreChildPage,
    MarketingCorePage,
    MarketingCoreSubpage,
)


class Command(BaseCommand):
    help = "Create default marketing core pages and a sample blog post (idempotent)."

    def handle(self, *args, **options):
        created = {"pages": 0, "subpages": 0, "children": 0, "blogs": 0}

        # ---- Help Centre → getting started → (create a store) --------------
        help_page, made = MarketingCorePage.objects.get_or_create(
            slug="help-centre",
            defaults={
                "title": "Help Centre",
                "order": 1,
                "content": "<p>Guides and answers for running your OneWooz store.</p>",
            },
        )
        created["pages"] += int(made)

        getting_started, made = MarketingCoreSubpage.objects.get_or_create(
            core_page=help_page,
            slug="getting-started",
            defaults={
                "title": "Getting started",
                "order": 1,
                "content": "<p>Everything you need to launch your first store.</p>",
            },
        )
        created["subpages"] += int(made)

        _, made = MarketingCoreChildPage.objects.get_or_create(
            subpage=getting_started,
            slug="create-a-store",
            defaults={
                "title": "Create a store",
                "order": 1,
                "content": (
                    "<p>Pick a business category and subcategory, name your brand, "
                    "and OneWooz builds a working storefront you can refine.</p>"
                ),
            },
        )
        created["children"] += int(made)

        # ---- Company → about ----------------------------------------------
        company, made = MarketingCorePage.objects.get_or_create(
            slug="company",
            defaults={
                "title": "Company",
                "order": 2,
                "content": "<p>About OneWooz and the team building it.</p>",
            },
        )
        created["pages"] += int(made)

        _, made = MarketingCoreSubpage.objects.get_or_create(
            core_page=company,
            slug="about",
            defaults={
                "title": "About us",
                "order": 1,
                "content": "<p>Commerce infrastructure for people who would rather run their business than manage a website.</p>",
            },
        )
        created["subpages"] += int(made)

        # ---- Sample blog post ---------------------------------------------
        _, made = MarketingBlog.objects.get_or_create(
            slug="welcome-to-onewooz",
            defaults={
                "title": "Welcome to OneWooz",
                "author": "OneWooz",
                "excerpt": "What OneWooz is and how it helps you sell online in minutes.",
                "content": (
                    "<p>OneWooz turns a few answers about your business into a live, "
                    "working online store  products, checkout, payments and shipping "
                    "included. This blog is where we'll share product updates and guides.</p>"
                ),
            },
        )
        created["blogs"] += int(made)

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded marketing content  "
                f"pages: {created['pages']}, subpages: {created['subpages']}, "
                f"child pages: {created['children']}, blogs: {created['blogs']} "
                "(existing rows left untouched)."
            )
        )
