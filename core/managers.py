"""Tenant scoping that is enforced rather than remembered.

In the platform this product replaces, isolation between tenants was entirely a
matter of every view remembering to write ``.filter(website=website)``. With
235 view functions in a single module, that is a promise nobody can keep  one
omission anywhere exposes one merchant's orders to another.

Here, a queryset over a tenant-scoped model refuses to evaluate unless it has
been narrowed to a site. Forgetting raises loudly in development and in tests
instead of silently returning every tenant's rows.

Three ways to get a usable queryset::

    Product.objects.for_site(site)          # the normal path
    site.products.all()                     # related managers are pre-scoped
    Product.objects.across_all_sites()      # deliberate, greppable, rare

``across_all_sites`` exists for the cases that genuinely need it  platform
admin views, billing runs, sitemap generation for every store  and it reads
like the exception it is.
"""

from __future__ import annotations

from django.db import models

__all__ = [
    "SiteScopedQuerySet",
    "SiteScopedManager",
    "UnscopedQueryError",
]


class UnscopedQueryError(RuntimeError):
    """A tenant-scoped queryset was evaluated without being narrowed to a site."""


class SiteScopedQuerySet(models.QuerySet):
    """A queryset that must be told which site it belongs to."""

    _site_scoped: bool = False

    # -- scoping entry points ---------------------------------------------

    def for_site(self, site):
        """Narrow to one site. ``site`` may be a ``Site`` instance or its id."""
        site_id = getattr(site, "pk", site)
        clone = self.filter(**{f"{self.model.SITE_FIELD}_id": site_id})
        clone._site_scoped = True
        return clone

    def for_sites(self, sites):
        """Narrow to several sites  used by owner dashboards spanning stores."""
        ids = [getattr(s, "pk", s) for s in sites]
        clone = self.filter(**{f"{self.model.SITE_FIELD}_id__in": ids})
        clone._site_scoped = True
        return clone

    def across_all_sites(self):
        """Opt out of scoping deliberately.

        Only for platform-level work: admin tooling, billing runs, scheduled
        jobs that must see every tenant. Never reachable from a request handled
        on the app or storefront surface.
        """
        clone = self._clone()
        clone._site_scoped = True
        return clone

    # -- plumbing ----------------------------------------------------------

    def _clone(self, **kwargs):
        clone = super()._clone(**kwargs)
        clone._site_scoped = self._site_scoped
        return clone

    def _require_scope(self):
        if not self._site_scoped:
            raise UnscopedQueryError(
                f"{self.model.__name__} is tenant-scoped and this queryset was "
                f"never narrowed to a site. Use .for_site(site), or "
                f".across_all_sites() if crossing tenants is genuinely intended."
            )

    # Every path that reaches the database goes through one of these.

    def _fetch_all(self):
        self._require_scope()
        super()._fetch_all()

    def count(self):
        self._require_scope()
        return super().count()

    def exists(self):
        self._require_scope()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._require_scope()
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        self._require_scope()
        return super().update(**kwargs)

    def delete(self):
        self._require_scope()
        return super().delete()

    def in_bulk(self, *args, **kwargs):
        self._require_scope()
        return super().in_bulk(*args, **kwargs)


class SiteScopedManager(models.Manager.from_queryset(SiteScopedQuerySet)):
    """Default manager for tenant-scoped models."""

    def get_queryset(self):
        queryset = super().get_queryset()
        # Related managers (``site.products``, ``category.products``) are
        # constructed with an ``instance`` and always apply the relation's
        # filter, so they are already confined to one parent row. Requiring an
        # explicit .for_site() there would be noise, not safety.
        if getattr(self, "instance", None) is not None:
            queryset._site_scoped = True
        return queryset
