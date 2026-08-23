"""ASGI entrypoint for OneWooz."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onewooz.settings.prod")

application = get_asgi_application()
