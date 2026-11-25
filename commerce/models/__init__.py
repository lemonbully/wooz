"""Commerce data model.

Split across focused modules rather than the reference's single 2,900-line
``models.py``. Every model here is tenant-scoped: it inherits
``core.models.TenantScopedModel`` and carries a ``site`` foreign key, so a
queryset cannot be evaluated until narrowed with ``.for_site(site)``.
"""

from .catalog import (
    Category,
    Product,
    ProductImage,
    ProductFieldTooltip,
    ProductPriceChange,
    Review,
    SlugRedirect,
    TrustBadge,
    display_ordered_categories,
    renamed_slug,
)
from .config import PaymentGatewayConfig, TrackingConfig
from .media import MediaAsset
from .customers import (
    AbandonedCart,
    Customer,
    CustomerAddress,
    CustomerCredit,
    NewsletterSubscriber,
    WishlistItem,
)
from .sales import (
    Coupon,
    Enquiry,
    Order,
    OrderItem,
    OrderRequest,
    Payment,
)
from .billing import Bill, BillingConfig, OrderCharge
from .logistics import CourierWebhookEvent, Shipment, ShippingProvider, Warehouse
from .marketing import (
    AnalyticsEvent,
    AnalyticsSession,
    Collaboration,
    CollaborationMedia,
    CollaborationPayoutRequest,
    CollaborationProductImage,
    GoogleMerchantConfig,
    GoogleMerchantProductStatus,
)
from .team import StaffMember
from .quantity import QuantityDiscountRule, QuantityDiscountSettings
from .navigation import NavSeen

__all__ = [
    "NavSeen",
    "Category",
    "Product",
    "ProductImage",
    "ProductFieldTooltip",
    "ProductPriceChange",
    "Review",
    "SlugRedirect",
    "TrustBadge",
    "display_ordered_categories",
    "renamed_slug",
    "MediaAsset",
    "PaymentGatewayConfig",
    "TrackingConfig",
    "AbandonedCart",
    "Customer",
    "CustomerAddress",
    "CustomerCredit",
    "NewsletterSubscriber",
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
    "CourierWebhookEvent",
    "Shipment",
    "ShippingProvider",
    "Warehouse",
    "AnalyticsEvent",
    "AnalyticsSession",
    "Collaboration",
    "CollaborationMedia",
    "CollaborationPayoutRequest",
    "CollaborationProductImage",
    "GoogleMerchantConfig",
    "GoogleMerchantProductStatus",
    "StaffMember",
    "QuantityDiscountRule",
    "QuantityDiscountSettings",
]
