"""Per-surface URL configurations.

There is no single root urlconf. :class:`core.middleware.HostRoutingMiddleware`
picks one of these per request based on the Host header, so a storefront
request can never resolve a dashboard route and vice versa  isolation is
structural rather than a matter of remembering a permission check.
"""

MARKETING = "onewooz.urls.marketing"
APP = "onewooz.urls.app"
ADMIN = "onewooz.urls.admin"
STOREFRONT = "onewooz.urls.storefront"
