"""Generate example blog posts for onewooz.com.

Creates complete articles from ``web.sample_content.blog_posts``: title,
excerpt, author, staggered publish dates, full HTML body with links into the
Help Centre, and a generated cover image, so the blog index and every article
look finished on a fresh environment.

Usage::

    python manage.py generate_blogs                  # create what is missing
    python manage.py generate_blogs --dry-run        # preview, write nothing
    python manage.py generate_blogs --limit 4        # only the 4 newest posts
    python manage.py generate_blogs --update         # also refresh existing posts
    python manage.py generate_blogs --no-covers      # skip cover images
    python manage.py generate_blogs --drafts         # new posts start unpublished

Idempotent: posts are matched by slug. Without ``--update`` an existing post is
left exactly as it is. ``--update`` overwrites title, excerpt, author and body,
and adds a cover only where the post has none; the publish date, publish switch
and any cover uploaded in the admin are kept.

Run ``generate_core_pages`` as well: the articles link into those pages.
"""

from __future__ import annotations

import io
import math
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from web.models import MarketingBlog
from web.sample_content import clean
from web.sample_content.blog_posts import BLOG_POSTS

_MARK = {"created": "+", "updated": "~", "unchanged": "=", "kept": "="}


class Command(BaseCommand):
    help = "Generate example blog posts (with cover images) for onewooz.com (idempotent; matched by slug)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            metavar="N",
            help=f"Generate only the N most recent posts (there are {len(BLOG_POSTS)}).",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite the title, excerpt, author and body of posts that already exist.",
        )
        parser.add_argument(
            "--no-covers",
            action="store_true",
            help="Do not generate cover images.",
        )
        parser.add_argument(
            "--drafts",
            action="store_true",
            help="Create new posts unpublished, to review in the admin before they go live.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created or updated, then roll everything back.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        update = options["update"]
        dry_run = options["dry_run"]
        # Dry runs never touch file storage: a rolled-back row must not leave a file.
        covers = not options["no_covers"] and not dry_run

        posts = sorted(BLOG_POSTS, key=lambda p: p["days_ago"])  # newest first
        if options["limit"] is not None:
            if options["limit"] < 1:
                raise CommandError("--limit must be 1 or more.")
            posts = posts[: options["limit"]]

        counts = {"created": 0, "updated": 0, "unchanged": 0, "kept": 0}
        now = timezone.now()

        with transaction.atomic():
            for data in posts:
                post, state = self._upsert(data, now, update, not options["drafts"])
                cover_note = ""
                if covers and not post.cover_image:
                    _attach_cover(post, data)
                    cover_note = " + cover"
                    if state in ("unchanged", "kept"):
                        state = "updated"
                counts[state] += 1
                self._line(post, state, cover_note)
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("")
        unchanged = counts["unchanged"] + counts["kept"]
        self.stdout.write(
            f"Posts: {counts['created']} created, {counts['updated']} updated, {unchanged} unchanged"
        )
        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry run: nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Blog posts generated. Browse them at /blog/."))
        if counts["kept"] and not update:
            self.stdout.write("Existing posts were left untouched. Use --update to refresh their content.")

    # ------------------------------------------------------------------
    def _upsert(self, data: dict, now, update: bool, published: bool):
        fields = {
            "title": data["title"],
            "excerpt": data["excerpt"],
            "author": data["author"],
            "content": clean(data["content"]),
        }
        try:
            post = MarketingBlog.objects.get(slug=data["slug"])
        except MarketingBlog.DoesNotExist:
            post = MarketingBlog(
                slug=data["slug"],
                is_published=published,
                published_at=now - timedelta(days=data["days_ago"], hours=data["days_ago"] % 7),
                **fields,
            )
            post.save()
            return post, "created"

        if not update:
            return post, "kept"
        changed = [name for name, value in fields.items() if getattr(post, name) != value]
        if not changed:
            return post, "unchanged"
        for name in changed:
            setattr(post, name, fields[name])
        post.save()
        return post, "updated"

    def _line(self, post: MarketingBlog, state: str, note: str) -> None:
        style = {"created": self.style.SUCCESS, "updated": self.style.WARNING}.get(state, lambda s: s)
        label = f"{_MARK[state]} {post.title}"
        date = post.published_at.strftime("%d %b %Y")
        self.stdout.write(style(f"{label:<66} {date:<12} {post.get_absolute_url():<52} {state}{note}"))


# ----------------------------------------------------------------------
# Cover images
# ----------------------------------------------------------------------
# A 1600×900 abstract cover per post, drawn with Pillow (already a dependency):
# a diagonal two-colour gradient, a hairline grid and concentric rings placed
# differently for each post. No text, so it never clashes with the title the
# page renders beside it, and no external image service is involved.


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _render_cover(slug: str, colours: tuple[str, str]) -> bytes:
    from PIL import Image, ImageDraw

    width, height = 1600, 900
    start, end = _hex(colours[0]), _hex(colours[1])

    # Diagonal gradient: build one row per step of the diagonal, then resize.
    small = Image.new("RGB", (160, 90))
    px = small.load()
    for y in range(90):
        for x in range(160):
            t = (x / 159 * 0.65) + (y / 89 * 0.35)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(start, end))
    image = small.resize((width, height), Image.BICUBIC)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Hairline grid.
    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=(255, 255, 255, 22), width=1)
    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=(255, 255, 255, 22), width=1)

    # Concentric rings, positioned from the slug so each post differs.
    seed = sum(ord(ch) * (i + 1) for i, ch in enumerate(slug))
    cx = 380 + seed % 840
    cy = 220 + (seed // 7) % 460
    for i in range(1, 9):
        r = 70 * i
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 60 - i * 5), width=2)
    draw.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], fill=(255, 255, 255, 210))

    # A short arc of dots in the opposite corner for balance.
    ox, oy = width - cx, height - cy
    for i in range(14):
        angle = math.radians(200 + i * 10)
        dx, dy = ox + 260 * math.cos(angle), oy + 260 * math.sin(angle)
        draw.ellipse([dx - 5, dy - 5, dx + 5, dy + 5], fill=(255, 255, 255, 120))

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True, progressive=True)
    return buffer.getvalue()


def _attach_cover(post: MarketingBlog, data: dict) -> None:
    content = _render_cover(data["slug"], data["cover"])
    post.cover_image.save(f"{data['slug']}.jpg", ContentFile(content), save=False)
    post.save(update_fields=["cover_image", "updated_at"])
