"""Template context for the owner dashboard's billing popups."""

from __future__ import annotations

from . import agreement_update


def agreement_update_gate(request):
    """Expose ``pending_agreement`` (an updated agreement the owner must accept).

    Lazy: the check only runs when a template actually reads the variable, which
    only the dashboard shells do. Every other surface pays nothing.
    """

    class _Lazy:
        _value = None
        _done = False

        def _get(self):
            if not self._done:
                self._value = agreement_update.pending_agreement(request)
                self._done = True
            return self._value

        def __bool__(self):
            return self._get() is not None

        def __getattr__(self, name):
            value = self._get()
            if value is None:
                raise AttributeError(name)
            return getattr(value, name)

    return {"pending_agreement": _Lazy()}
