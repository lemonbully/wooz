"""Code modules: genuinely new behaviour an App can borrow.

A module is written once by a developer. From then on staff can configure it
into any number of Apps from the admin (``App.module``), with any price and any
placement. Modules never see a request on a storefront: they provide dashboard
pages (app surface), storefront widget HTML (run at *compile* time, never at
render time) and lifecycle hooks (run on the ``marketplace`` Celery queue).

Every hook is called through :func:`call_hook`, which catches, records the
error against the app (circuit breaker) and never raises into the caller.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("onewooz.marketplace")

__all__ = ["Module", "REGISTRY", "get", "choices", "call_hook"]


class Module:
    """Base class. Override what you need; every method has a safe default."""

    key: str = ""
    name: str = ""
    description: str = ""

    def items(self) -> list[dict]:
        """Items defined in code, synced into ``AppItem`` rows (never edited after)."""
        return []

    def pages(self) -> dict:
        """``{page_key: callable(request, site, app, placement) -> HttpResponse}``."""
        return {}

    def page_choices(self) -> list[tuple[str, str]]:
        return [(k, k) for k in self.pages()]

    def home(self, request, site, app):
        """The app's own dashboard page, opened from its sidebar link.
        ``None``: it opens its first page or its settings instead."""
        return None

    def render_widget(self, *, site, app, placement, values: dict) -> str:
        """Storefront HTML for a ``module_widget`` placement (compile time)."""
        return ""

    # -- lifecycle (best-effort, async) ---------------------------------
    def on_install(self, *, owner_ref, owner_email, app) -> None:
        return None

    def on_uninstall(self, *, owner_ref, owner_email, app) -> None:
        return None

    def on_licence_change(self, *, licence, event: str) -> None:
        return None

    def on_settings_saved(self, *, site, app, config) -> None:
        return None


def _load() -> dict[str, Module]:
    from .magic_seo import MagicSeo
    from .quantity_discounts import QuantityDiscounts
    from .storefront_designs import StorefrontDesigns

    mods = [StorefrontDesigns(), QuantityDiscounts(), MagicSeo()]
    return {m.key: m for m in mods}


_REGISTRY: dict[str, Module] | None = None


def registry() -> dict[str, Module]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load()
    return _REGISTRY


REGISTRY = registry  # callable alias, kept short for callers


def get(key: str) -> Module | None:
    if not key:
        return None
    return registry().get(key)


def choices() -> list[tuple[str, str]]:
    return [("", "No module (configuration only)")] + [(m.key, m.name) for m in registry().values()]


def call_hook(app, hook: str, **kwargs) -> None:
    """Run a module hook for ``app``; never raises."""
    module = get(getattr(app, "module", ""))
    if module is None:
        return
    fn = getattr(module, hook, None)
    if fn is None:
        return
    try:
        fn(app=app, **kwargs) if hook in ("on_install", "on_uninstall") else fn(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("marketplace hook %s failed for %s", hook, getattr(app, "slug", "?"))
        try:
            from .. import health

            health.record_error(app, "hook", exc, {"hook": hook})
        except Exception:
            pass
