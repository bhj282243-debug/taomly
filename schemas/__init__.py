"""
schemas/__init__.py — Taomly Platform

Explicit re-exports for backward compatibility.
All existing `from schemas import X` continue to work unchanged.

Internal validator functions (_validate_*) are NOT exported here —
they are used only within submodules via direct import from schemas.common.
"""

# ── Analytics ────────────────────────────────────────────────────────────────
from schemas.analytics import (
    DayRevenueItem,
    DishItem,
    HourItem,
    OrderTypeItem,
    SummaryResponse,
)

# ── Auth ─────────────────────────────────────────────────────────────────────
from schemas.auth import (
    AgencyLogin,
    AgencyRegister,
    AgencyResponse,
    RestaurantAdminLogin,
    RestaurantAdminResponse,
    RestaurantCreate,
    RestaurantCreateResponse,
    RestaurantUpdate,
    TokenResponse,
)

# ── Billing ──────────────────────────────────────────────────────────────────
from schemas.billing import (
    PlanResponse,
    SubscribeResponse,
    SubscriptionResponse,
    UsageResponse,
)

# ── Localization ─────────────────────────────────────────────────────────────
from schemas.localization import (
    CategoryTranslationResponse,
    CategoryTranslationUpsert,
    NameTranslationResponse,
    NameTranslationUpsert,
    ProductTranslationResponse,
    ProductTranslationUpsert,
)

# ── Menu (admin CRUD) ─────────────────────────────────────────────────────────
from schemas.menu import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    ModifierGroupCreate,
    ModifierGroupResponse,
    ModifierGroupUpdate,
    ModifierOptionCreate,
    ModifierOptionResponse,
    ModifierOptionUpdate,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    VariantCreate,
    VariantResponse,
    VariantUpdate,
)

# ── Menu Public (WithTranslations) ────────────────────────────────────────────
from schemas.menu_public import (
    CategoryResponseWithTranslations,
    ModifierGroupResponseWithTranslations,
    ModifierOptionResponseWithTranslations,
    ProductResponseWithTranslations,
    VariantResponseWithTranslations,
)

# ── Orders ───────────────────────────────────────────────────────────────────
from schemas.orders import (
    OrderCreate,
    OrderItemCreate,
    OrderItemResponse,
    OrderResponse,
    OrderStatusUpdate,
    SelectedModifierResponse,
)

# ── Reservations & Waiter Calls ───────────────────────────────────────────────
from schemas.reservations import (
    ReservationCreate,
    ReservationResponse,
    ReservationStatusUpdate,
    WaiterCallCreate,
    WaiterCallResponse,
    WaiterCallStatusUpdate,
)

# ── Restaurant (public API, tables, locations) ────────────────────────────────
from schemas.restaurant import (
    CategoryPublicResponse,
    LocationCreate,
    LocationListResponse,
    LocationResponse,
    LocationUpdate,
    ModifierGroupPublicResponse,
    ModifierOptionPublicResponse,
    ProductPublicResponse,
    RestaurantPublicResponse,
    RestaurantSettingsResponse,
    RestaurantSettingsUpdateResponse,
    TableCreateRequest,
    TableCreateResponse,
    TableItem,
    TableResponse,
    TablesListResponse,
    VariantPublicResponse,
)

# ── Superadmin ────────────────────────────────────────────────────────────────
from schemas.superadmin import (
    SAAgencyCreateResponse,
    SAAgencyDetailResponse,
    SAAgencyDetailRestaurant,
    SAAgencyItem,
    SAAgencyListResponse,
    SAAgencyUpdateResponse,
    SADashboardCounters,
    SADashboardResponse,
    SAFreezeResponse,
    SAImpersonateResponse,
    SARecentAgencyItem,
    SARecentRestaurantItem,
    SARestaurantItem,
    SARestaurantListResponse,
    SATransferResponse,
)

__all__ = [
    # Analytics
    "DayRevenueItem", "DishItem", "HourItem", "OrderTypeItem", "SummaryResponse",
    # Auth
    "AgencyLogin", "AgencyRegister", "AgencyResponse",
    "RestaurantAdminLogin", "RestaurantAdminResponse",
    "RestaurantCreate", "RestaurantCreateResponse", "RestaurantUpdate", "TokenResponse",
    # Billing
    "PlanResponse", "SubscribeResponse", "SubscriptionResponse", "UsageResponse",
    # Localization
    "CategoryTranslationResponse", "CategoryTranslationUpsert",
    "NameTranslationResponse", "NameTranslationUpsert",
    "ProductTranslationResponse", "ProductTranslationUpsert",
    # Menu
    "CategoryCreate", "CategoryResponse", "CategoryUpdate",
    "ModifierGroupCreate", "ModifierGroupResponse", "ModifierGroupUpdate",
    "ModifierOptionCreate", "ModifierOptionResponse", "ModifierOptionUpdate",
    "ProductCreate", "ProductResponse", "ProductUpdate",
    "VariantCreate", "VariantResponse", "VariantUpdate",
    # Menu Public
    "CategoryResponseWithTranslations", "ModifierGroupResponseWithTranslations",
    "ModifierOptionResponseWithTranslations", "ProductResponseWithTranslations",
    "VariantResponseWithTranslations",
    # Orders
    "OrderCreate", "OrderItemCreate", "OrderItemResponse",
    "OrderResponse", "OrderStatusUpdate", "SelectedModifierResponse",
    # Reservations & Waiter Calls
    "ReservationCreate", "ReservationResponse", "ReservationStatusUpdate",
    "WaiterCallCreate", "WaiterCallResponse", "WaiterCallStatusUpdate",
    # Restaurant
    "CategoryPublicResponse", "LocationCreate", "LocationListResponse",
    "LocationResponse", "LocationUpdate",
    "ModifierGroupPublicResponse", "ModifierOptionPublicResponse",
    "ProductPublicResponse", "RestaurantPublicResponse",
    "RestaurantSettingsResponse", "RestaurantSettingsUpdateResponse",
    "TableCreateRequest", "TableCreateResponse", "TableItem",
    "TableResponse", "TablesListResponse", "VariantPublicResponse",
    # Superadmin
    "SAAgencyCreateResponse", "SAAgencyDetailResponse", "SAAgencyDetailRestaurant",
    "SAAgencyItem", "SAAgencyListResponse", "SAAgencyUpdateResponse",
    "SADashboardCounters", "SADashboardResponse", "SAFreezeResponse",
    "SAImpersonateResponse", "SARecentAgencyItem", "SARecentRestaurantItem",
    "SARestaurantItem", "SARestaurantListResponse", "SATransferResponse",
]
