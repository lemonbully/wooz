from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Importing registers the system checks that enforce tenant scoping.
        from . import checks  # noqa: F401

        # Connect the project-wide pre_save receiver that transcodes every newly
        # uploaded raster image to WebP, on any model's file field, from any
        # source. See core.image_webp for why it is one global signal.
        from . import image_webp

        image_webp.connect()
