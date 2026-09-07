"""
models/__init__.py — Taomly Platform
Package entry point: регистрирует все ORM-классы в едином Base.metadata
и обеспечивает полную обратную совместимость со всеми существующими импортами.

Совместимость:
  from models import Agency            # ✓
  from models import Restaurant        # ✓
  from models import Product           # ✓
  from models import Order             # ✓
  from models import MENU_LANGUAGES    # ✓
  import models                        # ✓ — используется в api.py и alembic/env.py
                                       #     для регистрации всех классов в Base.metadata

Порядок импортов:
  Все submodules используют один Base из database.py.
  SQLAlchemy регистрирует классы в mapper registry при импорте.
  Порядок здесь гарантирует, что при string-based relationship resolution
  (например "Restaurant", "ProductVariant") все классы уже зарегистрированы.
"""

# ── Constants ─────────────────────────────────────────────────────────────────
from .base import MENU_LANGUAGES

# ── Auth ──────────────────────────────────────────────────────────────────────
from .auth import RevokedToken

# ── Billing (нет FK на restaurant/location кроме строковых ref) ───────────────
from .billing import SubscriptionPlan, Subscription, UsageEvent

# ── Tenant (Agency → Restaurant → Location, User) ─────────────────────────────
from .tenant import Agency, Restaurant, Location, User

# ── Menu (зависит от tenant через string refs) ────────────────────────────────
from .menu import (
    Category,
    Product,
    ProductVariant,
    ModifierGroup,
    ModifierOption,
)

# ── Menu i18n / localization (зависит от menu через string refs) ──────────────
from .menu_i18n import (
    CategoryTranslation,
    ProductTranslation,
    VariantTranslation,
    ModifierGroupTranslation,
    ModifierOptionTranslation,
)

# ── Operations (зависит от tenant через string refs) ──────────────────────────
from .operations import RestaurantTable, Reservation, WaiterCall

# ── Orders (зависит от tenant, menu, operations через string refs) ─────────────
from .orders import Order, OrderItem, OrderItemModifier

# ── Public API ────────────────────────────────────────────────────────────────
__all__ = [
    # constants
    "MENU_LANGUAGES",
    # auth
    "RevokedToken",
    # billing
    "SubscriptionPlan",
    "Subscription",
    "UsageEvent",
    # tenant
    "Agency",
    "Restaurant",
    "Location",
    "User",
    # menu
    "Category",
    "Product",
    "ProductVariant",
    "ModifierGroup",
    "ModifierOption",
    # menu i18n
    "CategoryTranslation",
    "ProductTranslation",
    "VariantTranslation",
    "ModifierGroupTranslation",
    "ModifierOptionTranslation",
    # operations
    "RestaurantTable",
    "Reservation",
    "WaiterCall",
    # orders
    "Order",
    "OrderItem",
    "OrderItemModifier",
]
