"""System checks that enforce this codebase's own invariants.

Run automatically by Django on startup and by ``manage.py check``. These turn
conventions that would otherwise live in a style guide into failures a
developer cannot miss.
"""

from __future__ import annotations

from django.apps import apps
from django.core.checks import Error, register

from .managers import SiteScopedManager
from .models import TenantScopedModel


@register()
def tenant_scoped_models_declare_site_fk(app_configs, **kwargs):
    """Every TenantScopedModel must actually have its site foreign key.

    The base class cannot declare the field itself without ``core`` depending
    on ``tenancy``, so this verifies at startup what would otherwise only fail
    at the first query.
    """
    errors = []
    for model in apps.get_models():
        if not issubclass(model, TenantScopedModel) or model._meta.abstract:
            continue

        field_name = model.SITE_FIELD
        try:
            field = model._meta.get_field(field_name)
        except Exception:
            errors.append(
                Error(
                    f"{model._meta.label} inherits TenantScopedModel but has no "
                    f"{field_name!r} field.",
                    hint=(
                        f"Add: {field_name} = models.ForeignKey('tenancy.Site', "
                        f"on_delete=models.CASCADE, related_name='...')"
                    ),
                    obj=model,
                    id="core.E001",
                )
            )
            continue

        if not field.is_relation or field.many_to_many:
            errors.append(
                Error(
                    f"{model._meta.label}.{field_name} must be a ForeignKey to "
                    f"tenancy.Site.",
                    obj=model,
                    id="core.E002",
                )
            )
    return errors


@register()
def tenant_scoped_models_keep_scoped_manager(app_configs, **kwargs):
    """The default manager of a tenant-scoped model must enforce scoping.

    Declaring ``objects = models.Manager()`` on a subclass would quietly
    reopen every cross-tenant query this design exists to prevent.
    """
    errors = []
    for model in apps.get_models():
        if not issubclass(model, TenantScopedModel) or model._meta.abstract:
            continue
        if not isinstance(model._default_manager, SiteScopedManager):
            errors.append(
                Error(
                    f"{model._meta.label}'s default manager is "
                    f"{type(model._default_manager).__name__}, which does not "
                    f"enforce tenant scoping.",
                    hint="Leave `objects` inherited from TenantScopedModel, or "
                    "subclass SiteScopedManager.",
                    obj=model,
                    id="core.E003",
                )
            )
    return errors
