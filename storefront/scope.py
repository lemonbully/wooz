"""Catalogue scoping for UGC collaboration (referral) storefronts.

A collaboration store (``<subdomain>.myugcstore.com``) sells the *parent* store's
catalogue, but only the slice the owner assigned to that collaborator. When the
request carries a ``collaboration`` (set by ``storefront.resolver``), these
helpers narrow the product / category querysets to the collaborator's scope:

* ``product_scope == "specific"`` → only the products explicitly linked to the
  collaboration.
* ``category_scope == "specific"`` → only categories linked to the collaboration
  (and products within them).

On a normal storefront request (no collaboration) they are a pass-through, so
the owner's own store is never affected.
"""

from __future__ import annotations


def collaboration_for(request):
    """The collaboration attached to a referral storefront request, or None."""
    return getattr(request, "collaboration", None)


def _specific_category_ids(collab) -> list:
    return list(collab.categories.values_list("id", flat=True))


def scope_products(qs, request):
    """Narrow a product queryset to the request's collaboration scope."""
    collab = collaboration_for(request)
    if collab is None:
        return qs
    Scope = type(collab).Scope
    if collab.product_scope == Scope.SPECIFIC:
        qs = qs.filter(collaborations=collab)
    if collab.category_scope == Scope.SPECIFIC:
        qs = qs.filter(category_id__in=_specific_category_ids(collab))
    return qs


def scope_categories(qs, request):
    """Narrow a category queryset to the request's collaboration scope."""
    collab = collaboration_for(request)
    if collab is None:
        return qs
    Scope = type(collab).Scope
    if collab.category_scope == Scope.SPECIFIC:
        qs = qs.filter(id__in=_specific_category_ids(collab))
    return qs
