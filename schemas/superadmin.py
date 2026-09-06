"""
schemas/superadmin.py — Taomly Platform

Superadmin panel response schemas (SA* prefix).
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


# ──────────────────────────────────────────
# SUPERADMIN — response models
# ──────────────────────────────────────────

class SAAgencyItem(BaseModel):
    id:               int
    name:             str
    email:            str
    is_active:        bool
    created_at:       str
    restaurant_count: int

    model_config = ConfigDict(from_attributes=True)


class SAAgencyListResponse(BaseModel):
    total: int
    items: List[SAAgencyItem]


class SAAgencyDetailRestaurant(BaseModel):
    id:         int
    name:       str
    slug:       str
    is_active:  bool
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class SAAgencyDetailResponse(BaseModel):
    id:          int
    name:        str
    email:       str
    is_active:   bool
    created_at:  str
    restaurants: List[SAAgencyDetailRestaurant]


class SAAgencyCreateResponse(BaseModel):
    id:    int
    name:  str
    email: str


class SAAgencyUpdateResponse(BaseModel):
    ok:        bool
    id:        int
    is_active: bool


class SAImpersonateResponse(BaseModel):
    access_token: str
    agency_name:  str


class SARestaurantItem(BaseModel):
    id:         int
    name:       str
    slug:       str
    address:    Optional[str] = None
    is_active:  bool
    agency_id:  int
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class SARestaurantListResponse(BaseModel):
    total: int
    items: List[SARestaurantItem]


class SAFreezeResponse(BaseModel):
    ok:        bool
    is_active: bool


class SATransferResponse(BaseModel):
    ok:            bool
    restaurant_id: int
    new_agency_id: int


class SARecentAgencyItem(BaseModel):
    id:               int
    name:             str
    email:            str
    is_active:        bool
    created_at:       str
    restaurant_count: int


class SARecentRestaurantItem(BaseModel):
    id:         int
    name:       str
    slug:       str
    is_active:  bool
    agency_id:  int
    created_at: str


class SADashboardCounters(BaseModel):
    total:          int
    active:         int
    inactive:       int
    new_this_month: int


class SADashboardResponse(BaseModel):
    agencies:            SADashboardCounters
    restaurants:         SADashboardCounters
    mrr:                 int
    arr:                 int
    recent_agencies:     List[SARecentAgencyItem]
    recent_restaurants:  List[SARecentRestaurantItem]
