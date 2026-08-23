from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Importing registers the system checks that enforce tenant scoping.
        from . import checks  # noqa: F401
