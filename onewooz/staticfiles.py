"""Production static-file minification, wired into ``collectstatic``.

Only the **production** settings select this storage backend, so development is
never touched: dev serves the hand-authored source files straight from disk
(WhiteNoise, no ``collectstatic``) and keeps the plain storage. On a production
deploy, ``collectstatic`` copies every static file to the S3 static bucket  and
this storage first **minifies** each authored ``.css`` and ``.js``: comments
stripped and whitespace collapsed onto essentially one line, so the assets
shipped to the browser are compact and not casually readable. The manifest layer
then content-hashes them exactly as before, so cache-busting and the
``{% static %}`` URLs are unchanged.

Why it minifies the *source* file, not the uploaded copy
--------------------------------------------------------
Django's manifest hashing (``HashedFilesMixin._post_process``) deliberately
reads each file from its **original, local** location  its own comment says
"use the original, local file, not the copied-but-unprocessed version, which
might be somewhere far away, like S3." So the served, content-hashed object is
built from the source on disk, never from whatever we may have re-written on S3.
The only way to make the *served* file minified is therefore to minify the
source file (on the container's ephemeral filesystem) **before** the hashing
pass reads it  which is what ``post_process`` does below, then delegates to the
manifest/S3 layer. This also keeps each file's hash matching its actual bytes.

Scope, deliberately:

* **Minification, not renaming.** rCSSmin / rjsmin only remove comments and
  whitespace  a transform that cannot change behaviour. They do **not** rename
  identifiers or mangle class names (that needs a JS parser / Node toolchain and
  would break templates that reference classes by name). Stronger obfuscation
  belongs in a separate, opt-in build stage.
* **CSS + JS only.** Images, fonts and the manifest pass through untouched.
  Files already minified (``*.min.css`` / ``*.min.js``) are skipped.
* **Never breaks a deploy.** A missing minifier or a per-file failure is logged
  and the original file is shipped as-is. The INFO line "static minify: minified
  N …" in the web logs confirms it ran.

Inline ``<script>``/``<style>`` blocks inside templates are HTML, not static
files, so they are outside this storage's reach (HTML minification was declared
out of scope). Everything in ``onewooz/static`` is covered.
"""

from __future__ import annotations

import logging
import os

# The production static backend. Imported defensively so this module can also be
# imported where django-storages is absent (it will simply never be selected as
# the backend there); production always has it.
try:  # pragma: no cover - exercised only under the production stack
    from storages.backends.s3 import S3ManifestStaticStorage as _BaseStaticStorage
except Exception:  # pragma: no cover
    from django.contrib.staticfiles.storage import (
        ManifestStaticFilesStorage as _BaseStaticStorage,
    )

logger = logging.getLogger("onewooz")

_MINIFY_EXTS = {".css", ".js"}
_ALREADY_MIN = (".min.css", ".min.js", "-min.css", "-min.js")


class MinifyingManifestStaticStorage(_BaseStaticStorage):
    """S3 manifest static storage that minifies CSS/JS source before hashing."""

    def post_process(self, paths, dry_run=False, **options):
        if not dry_run:
            self._minify_sources(paths)
        yield from super().post_process(paths, dry_run=dry_run, **options)

    def _minify_sources(self, paths):
        """Minify each ``.css``/``.js`` source file in place, on the local
        (ephemeral, in-container) filesystem, before the manifest layer reads it.

        ``paths`` maps ``name -> (source_storage, source_path)``. We resolve the
        source file's real path and rewrite it minified; if a source is not on a
        local filesystem, or a minifier is unavailable, we leave it untouched so
        the deploy still succeeds with the original file.
        """
        try:
            import rcssmin
            import rjsmin
        except Exception:
            # One clear line rather than a per-file storm  then ship originals.
            logger.error(
                "static minify DISABLED: rcssmin/rjsmin could not be imported; "
                "assets will be served un-minified",
                exc_info=True,
            )
            return

        # Only our own assets are rewritten. Third-party static files (Django
        # admin and other installed packages) live in the read-only virtualenv,
        # are already compact, and writing them raised a PermissionError
        # traceback per file on every deploy.
        from django.conf import settings

        project_root = os.path.realpath(str(settings.BASE_DIR))
        minified = skipped = 0
        for name, storage_path in paths.items():
            ext = os.path.splitext(name)[1].lower()
            if ext not in _MINIFY_EXTS:
                continue
            if os.path.basename(name).lower().endswith(_ALREADY_MIN):
                continue
            try:
                storage, path = storage_path
                source_path = storage.path(path)
            except (NotImplementedError, AttributeError, ValueError, TypeError):
                # Source isn't a local file (nothing to rewrite here).
                continue
            real = os.path.realpath(source_path)
            if not real.startswith(project_root + os.sep) or not os.access(real, os.W_OK):
                skipped += 1
                continue
            try:
                with open(source_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                out = rcssmin.cssmin(text) if ext == ".css" else rjsmin.jsmin(text)
                if out and out != text:
                    with open(source_path, "w", encoding="utf-8") as fh:
                        fh.write(out)
                    minified += 1
            except Exception:
                logger.warning("static minify skipped: %s", name, exc_info=True)
                continue

        logger.info(
            "static minify: minified %d CSS/JS file(s); left %d third-party/read-only file(s) as shipped",
            minified, skipped,
        )
