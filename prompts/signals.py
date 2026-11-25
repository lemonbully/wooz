"""Catalogue changes queue a store llms.txt regeneration.

Adding, editing, activating/deactivating, soft- or hard-deleting a product or a
category, and changing a product's additional categories, all change what the
store's llms.txt says (live products, prices, category names and counts). Each
queues the store's debounced job (:func:`prompts.llms_txt.schedule_site_id`)
after the transaction commits. The job compares a fingerprint of the data, so
a change that does not affect the file (for example a stock count) never spends
an API call.

Stock-only saves (orders deducting or restoring inventory) are skipped here
outright, so a busy store's orders do not queue jobs at all.
"""

from __future__ import annotations

from django.db import transaction

#: A product save touching only these fields is a stock movement, not a
#: catalogue change.
_STOCK_ONLY_FIELDS = frozenset({"inventory", "variant_combinations", "updated_at", "stock_on_hand"})


def _queue(site_id) -> None:
    if not site_id:
        return

    def go():
        try:
            from .llms_txt import schedule_site_id

            schedule_site_id(site_id)
        except Exception:
            pass  # never let llms.txt bookkeeping break a catalogue save

    transaction.on_commit(go)


def product_saved(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    if update_fields and set(update_fields) <= _STOCK_ONLY_FIELDS:
        return
    _queue(getattr(instance, "site_id", None))


def catalogue_deleted(sender, instance, **kwargs):
    _queue(getattr(instance, "site_id", None))


def category_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _queue(getattr(instance, "site_id", None))


def product_categories_changed(sender, instance, action, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    _queue(getattr(instance, "site_id", None))
