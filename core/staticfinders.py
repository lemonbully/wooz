"""Static file finders.

Django's ``FileSystemFinder`` matches a *prefixed* ``STATICFILES_DIRS`` entry
(``("storefront-t3", path)``) by checking ``path.startswith(prefix + os.sep)``.
Request paths always use ``/``, so on Windows (``os.sep == "\\"``) a prefixed
directory never matches and every file in it 404s in development, while
collectstatic and Linux servers are unaffected. This subclass normalises the
separator first, so prefixed directories resolve on every platform. It behaves
exactly like the stock finder otherwise (a no-op on Linux).
"""

from __future__ import annotations

import os

from django.contrib.staticfiles.finders import FileSystemFinder as _FileSystemFinder


class FileSystemFinder(_FileSystemFinder):
    def find_location(self, root, path, prefix=None):
        if prefix and os.sep != "/":
            path = path.replace("/", os.sep)
        return super().find_location(root, path, prefix)
