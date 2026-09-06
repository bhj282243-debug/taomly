"""
schemas/localization.py — Taomly Platform

Phase 4 — Menu Localization: translation request/response schemas.
Must be created before menu_public.py (dependency: localization → menu_public).
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────
# Translation request/response schemas
# ──────────────────────────────────────────

class CategoryTranslationUpsert(BaseModel):
    """PUT /api/menu/category/{id}/translations/{lang} — body."""
    name: str = Field(..., min_length=1, max_length=255)


class ProductTranslationUpsert(BaseModel):
    """PUT /api/menu/product/{id}/translations/{lang} — body."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class NameTranslationUpsert(BaseModel):
    """PUT translation body для Variant / ModifierGroup / ModifierOption."""
    name: str = Field(..., min_length=1, max_length=255)


class CategoryTranslationResponse(BaseModel):
    language: str
    name: str
    model_config = ConfigDict(from_attributes=True)


class ProductTranslationResponse(BaseModel):
    language: str
    name: str
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class NameTranslationResponse(BaseModel):
    """Response для Variant / ModifierGroup / ModifierOption переводов."""
    language: str
    name: str
    model_config = ConfigDict(from_attributes=True)
