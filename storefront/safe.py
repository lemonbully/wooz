"""Content that never raises during template rendering.

The ported storefront templates read hundreds of ``content.*`` keys, many inside
``|default:content.x`` / ``|add:product.id`` positions where Django resolves the
argument eagerly. A missing key there raises ``VariableDoesNotExist`` and takes
the whole page down  a store must never be undisplayable because one optional
field is blank.

``wrap`` deep-wraps a content blob so that:

* a present key returns its (wrapped) value, and
* a missing key  at any depth  returns :data:`MISSING`, a null object that
  renders as an empty string, is falsy, iterates empty, and supports further
  ``[key]`` / ``.attr`` access so long chains keep resolving instead of raising.

Combined with the demo-content merge in :func:`storefront.context.build_context`,
known keys show real placeholder content and everything else renders blank.
"""

from __future__ import annotations


class _Missing:
    """A null object standing in for any absent content value."""

    __slots__ = ()

    # Chained access keeps returning the null object rather than raising.
    def __getitem__(self, key):
        return MISSING

    def __getattr__(self, name):
        # Let genuine dunder / internal lookups behave normally so Django's
        # resolver and Python itself aren't misled; everything else is absent.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MISSING

    def __iter__(self):
        return iter(())

    def __contains__(self, item):
        return False

    def __len__(self):
        return 0

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __repr__(self):
        return ""

    def __html__(self):
        return ""

    # Numeric coercion so arithmetic filters (add, floatformat) treat it as 0.
    def __int__(self):
        return 0

    def __float__(self):
        return 0.0

    def __add__(self, other):
        return other

    def __radd__(self, other):
        return other

    # Comparisons never raise; a missing value equals other empties and sorts low.
    def __eq__(self, other):
        return other is MISSING or other in (None, "", 0, False)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return 0

    def __lt__(self, other):
        return True

    def __le__(self, other):
        return True

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return False

    def __call__(self, *args, **kwargs):
        return MISSING


MISSING = _Missing()


class SafeDict(dict):
    """A dict whose missing keys resolve to :data:`MISSING` instead of raising."""

    def __missing__(self, key):
        return MISSING

    # Django's resolver uses subscription, but keep ``get`` consistent for any
    # Python-side callers that pass no default.
    def get(self, key, default=MISSING):
        return super().get(key, default)


def wrap(obj):
    """Recursively wrap dicts/lists so lookups never raise."""
    if isinstance(obj, SafeDict):
        return obj
    if isinstance(obj, dict):
        return SafeDict({k: wrap(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return [wrap(v) for v in obj]
    return obj
