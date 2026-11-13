"""Sort-safe query helpers: keep MySQL sorts off wide rows.

Why this exists
---------------
When MySQL has to sort rows itself (an ``ORDER BY``, ``GROUP BY`` or
``DISTINCT`` that no index already delivers in order), it copies every selected
column of each row into the session's sort buffer (``sort_buffer_size``,
256 KB by default). A row that does not fit fails the whole query with error
1038, "Out of sort memory, consider increasing server sort buffer size".

Several models carry columns that grow without a practical bound:
``Site.content`` (the whole store: copy, banners, FAQs, blog posts), the email
``html`` on campaigns and templates, marketing blog bodies. Django loads every
column by default, and ``select_related`` adds the joined model's columns too,
so a perfectly ordinary query such as "this owner's stores, by name" or "due
campaigns with their site" works in development and then breaks in production
once one store's content is large enough. That is exactly how the Business Hub
went down.

The fixes, in order of preference
---------------------------------
1. An index that already returns rows in the requested order (MySQL then reads
   in order and never sorts). Best for hot lists; needs a migration.
2. :func:`sorted_rows` / :func:`first_row` / :func:`paginate_sorted`: the
   standard "late row lookup" (a.k.a. deferred join). Sort only the primary
   keys, which is a narrow sort however wide the row is, then load the full
   rows by primary key with no ``ORDER BY`` and restore the order in Python.
   The caller gets exactly the same objects, in exactly the same order, with
   ``select_related`` / ``prefetch_related`` / annotations and tenant scoping
   intact. Costs one extra, indexed query.
3. :func:`without_heavy_fields` / :func:`defer_related_heavy_fields`: leave the
   big columns out of the query altogether, where the caller provably does not
   read them (choice dropdowns that only need ``pk`` and ``__str__``). A field
   read later still loads lazily, so this can never return wrong data.

Rule for new code: never sort full rows of a model that has a JSON/TEXT column
(or ``select_related`` one) unless an index serves that exact order. Reach for
these helpers instead.
"""

from __future__ import annotations

from functools import lru_cache

from django.core.paginator import Page, Paginator
from django.db import models

__all__ = [
    "HEAVY_FIELD_TYPES",
    "heavy_fields",
    "without_heavy_fields",
    "defer_related_heavy_fields",
    "sorted_rows",
    "first_row",
    "paginate_sorted",
]

# Column types whose size has no practical upper bound. ``EncryptedTextField``
# subclasses ``TextField`` and is covered too.
HEAVY_FIELD_TYPES = (models.TextField, models.JSONField, models.BinaryField)

# Primary keys per "load by pk" query. Keeps the IN (...) list, and the packet
# carrying it, modest for very large result sets.
_PK_CHUNK = 1000


# ---------------------------------------------------------------------------
# Heavy columns
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def heavy_fields(model) -> tuple[str, ...]:
    """Names of ``model``'s concrete, unbounded-size columns (TEXT/JSON/BLOB)."""
    return tuple(
        f.name
        for f in model._meta.concrete_fields
        if isinstance(f, HEAVY_FIELD_TYPES) and not f.primary_key
    )


def without_heavy_fields(queryset, *, keep: tuple[str, ...] = ()):
    """``queryset`` with its own heavy columns deferred (loaded only on access).

    For querysets whose rows are only shown by ``pk`` / ``__str__``, such as a
    form's choice dropdown, which must stay a ``QuerySet``.
    """
    names = [n for n in heavy_fields(queryset.model) if n not in keep]
    return queryset.defer(*names) if names else queryset


def _related_model(model, path: str):
    """The model at the end of a forward relation path like ``campaign__drip``."""
    for part in path.split("__"):
        field = model._meta.get_field(part)
        if not (field.is_relation and (field.many_to_one or field.one_to_one)) or field.related_model is None:
            return None
        model = field.related_model
    return model


def defer_related_heavy_fields(queryset, *relations: str):
    """Join ``relations`` (forward FK / one-to-one paths) without their heavy columns.

    ``qs.select_related("site")`` pulls ``Site.content`` into every row; this
    keeps the join (so ``obj.site`` is still free) but leaves the big columns
    out. Any of them read later loads lazily for that one object.
    """
    deferred = []
    joined = []
    for path in relations:
        related = _related_model(queryset.model, path)
        if related is None:
            continue
        joined.append(path)
        deferred.extend(f"{path}__{name}" for name in heavy_fields(related))
    if not joined:
        return queryset
    # select_related is applied explicitly: deferring a related column is only
    # meaningful (and only honoured) when that relation is actually joined.
    queryset = queryset.select_related(*joined)
    return queryset.defer(*deferred) if deferred else queryset


# ---------------------------------------------------------------------------
# Late row lookup
# ---------------------------------------------------------------------------
def _load_in_order(queryset, ids: list) -> list:
    """Full rows of ``queryset`` for ``ids``, returned in the order of ``ids``.

    The rows are fetched with the ordering cleared, so MySQL never sorts them;
    every other aspect of ``queryset`` (filters, tenant scope, select_related,
    prefetch_related, annotations, distinct) is kept.
    """
    if not ids:
        return []
    base = queryset.order_by()
    unique = list(dict.fromkeys(ids))
    by_pk = {}
    for start in range(0, len(unique), _PK_CHUNK):
        for obj in base.filter(pk__in=unique[start:start + _PK_CHUNK]):
            by_pk[obj.pk] = obj
    # A row deleted between the two queries simply drops out, as it would have
    # had it been deleted a moment earlier.
    return [by_pk[pk] for pk in ids if pk in by_pk]


def _pks(queryset):
    """The primary keys of ``queryset``, in its order. Prefetches are dropped
    here (they would run against bare keys) and re-applied on the row load."""
    return queryset.prefetch_related(None).values_list("pk", flat=True)


def _check_model_queryset(queryset) -> None:
    if queryset.query.is_sliced:
        raise TypeError("Pass limit/offset to the helper instead of slicing the queryset.")
    if queryset._fields is not None:
        raise TypeError("Sort-safe helpers return model instances; drop .values()/.values_list().")


def sorted_rows(queryset, *, limit: int | None = None, offset: int = 0) -> list:
    """``list(queryset[offset:offset + limit])``, without sorting wide rows.

    Same objects, same order, same related data as evaluating ``queryset``
    directly; only the primary keys go through the sort.
    """
    _check_model_queryset(queryset)
    stop = None if limit is None else offset + max(0, int(limit))
    if not queryset.ordered:
        # Nothing is sorted, so there is nothing to protect.
        return list(queryset[offset:stop]) if (offset or stop is not None) else list(queryset)
    ids = _pks(queryset)
    if offset or stop is not None:
        ids = ids[offset:stop]
    return _load_in_order(queryset, list(ids))


def first_row(queryset):
    """``queryset.first()``, without sorting wide rows.

    Like ``QuerySet.first()``, an unordered queryset is ordered by primary key.
    """
    _check_model_queryset(queryset)
    if not queryset.ordered:
        queryset = queryset.order_by("pk")
    rows = sorted_rows(queryset, limit=1)
    return rows[0] if rows else None


def paginate_sorted(queryset, per_page: int, number, *, orphans: int = 0,
                    allow_empty_first_page: bool = True) -> Page:
    """``Paginator(queryset, per_page).get_page(number)``, without sorting wide rows.

    Counting and paging run over primary keys; only the requested page's rows
    are then loaded in full. The returned ``Page`` behaves as usual
    (``number``, ``has_next``, ``paginator.num_pages``, iteration).
    """
    _check_model_queryset(queryset)
    paginator = Paginator(
        _pks(queryset), per_page,
        orphans=orphans, allow_empty_first_page=allow_empty_first_page,
    )
    page = paginator.get_page(number)
    page.object_list = _load_in_order(queryset, list(page.object_list))
    return page
