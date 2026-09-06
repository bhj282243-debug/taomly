"""
schemas/billing.py — Taomly Platform

Billing, subscription, and usage response schemas.
"""

from typing import Optional

from pydantic import BaseModel


# ──────────────────────────────────────────
# BILLING SCHEMAS
# ──────────────────────────────────────────

class PlanResponse(BaseModel):
    id: int
    name: str
    price: int
    currency: str
    orders_per_month: int
    products_limit: int
    users_limit: int
    description: Optional[str]


class SubscriptionResponse(BaseModel):
    plan_id: int
    plan_name: str
    price: int
    currency: str
    orders_per_month: int
    products_limit: int
    started_at: str
    expires_at: Optional[str]
    is_active: bool


class UsageResponse(BaseModel):
    period: str
    orders_used: int
    orders_limit: int
    orders_remaining: int
    orders_pct: int
    products_used: int
    products_limit: int
    products_remaining: int
    products_pct: int


class SubscribeResponse(BaseModel):
    success: bool
    plan_id: int
    plan_name: str
    message: str
