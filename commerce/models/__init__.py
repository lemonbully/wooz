"""Commerce data model.

Split across focused modules rather than the reference's single 2,900-line
``models.py``. Every model here is tenant-scoped: it inherits
``core.models.TenantScopedModel`` and carries a ``site`` foreign key, so a
queryset cannot be evaluated until narrowed with ``.for_site(site)``.
"""

from .catalog import Category, Product, ProductImage, Review
from .config import PaymentGatewayConfig, TrackingConfig
from .customers import Customer, CustomerAddress, CustomerCredit, WishlistItem
from .sales import (
    Coupon,
    Enquiry,
    Order,
    OrderItem,
    OrderRequest,
    Payment,
)
from .billing import Bill, BillingConfig, OrderCharge
from .logistics import Shipment, ShippingProvider, Warehouse
from .marketing import (
    AnalyticsEvent,
    AnalyticsSession,
    Collaboration,
    GoogleMerchantConfig,
)

__all__ = [
    "Category",
    "Product",
    "ProductImage",
    "Review",
    "PaymentGatewayConfig",
    "TrackingConfig",
    "Customer",
    "CustomerAddress",
    "CustomerCredit",
    "WishlistItem",
    "Coupon",
    "Enquiry",
    "Order",
    "OrderItem",
    "OrderRequest",
    "Payment",
    "Bill",
    "BillingConfig",
    "OrderCharge",
    "Shipment",
    "ShippingProvider",
    "Warehouse",
    "AnalyticsEvent",
    "AnalyticsSession",
    "Collaboration",
    "GoogleMerchantConfig",
]
