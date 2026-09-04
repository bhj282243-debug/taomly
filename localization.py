"""
localization.py — Taomly Platform

Shared backend localization helpers for menu/entity translations.

Responsibility: menu data localization (DB Translation models → resolved strings).
NOT for UI-string translations — that belongs to i18n.py + i18n/*.json.

Public API:
    resolve_menu_lang(query_lang, active_location) -> str
    localized_name(translations, lang, base_name)  -> str
    localized_desc(translations, lang, base_desc)  -> Optional[str]
    apply_lang_to_menu(categories, lang)            -> None

Used by:
    routers/menu_public.py  — GET /{restaurant_id} (public menu)
    routers/restaurants.py  — GET /api/restaurants/{slug} (public slug endpoint)

Import graph:
    localization.py -> models.MENU_LANGUAGES -> database -> sqlalchemy
    No circular dependency risk (models.py does not import from localization.py).

R-2: extracted from routers/menu_public.py and routers/restaurants.py.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_menu_lang(
    query_lang: Optional[str],
    active_location,
) -> str:
    """
    Deterministic language resolution for public menu endpoints.

    Priority:
      1. Explicit ?lang= query param (already validated by FastAPI Literal)
      2. Location.language when valid (in MENU_LANGUAGES)
      3. "uz" — absolute fallback

    Timezone does not participate in language logic.

    Args:
        query_lang:      Value of ?lang= query param, or None.
        active_location: Active Location ORM object, or None.

    Returns:
        One of "uz", "ru", "en".
    """
    from models import MENU_LANGUAGES  # local import — avoids potential circular dependency

    if query_lang:
        return query_lang
    if active_location and active_location.language in MENU_LANGUAGES:
        return active_location.language
    return "uz"


def localized_name(translations: list, lang: str, base_name: str) -> str:
    """
    Returns the localized name for the given language, or base_name as fallback.

    Args:
        translations: List of Translation ORM objects with .language and .name attributes.
        lang:         Target language code ("uz", "ru", "en").
        base_name:    Original name to return if no translation exists for lang.

    Returns:
        Translated name string, never None.
    """
    for t in translations:
        if t.language == lang:
            return t.name
    return base_name


def localized_desc(translations: list, lang: str, base_desc) -> Optional[str]:
    """
    Returns the localized description for the given language, or base_desc as fallback.

    Args:
        translations: List of Translation ORM objects with .language and .description attributes.
        lang:         Target language code ("uz", "ru", "en").
        base_desc:    Original description to return if no translation exists (may be None).

    Returns:
        Translated description string, or None if base_desc is None and no translation found.
    """
    for t in translations:
        if t.language == lang:
            return t.description
    return base_desc


def apply_lang_to_menu(categories: list, lang: str) -> None:
    """
    Applies localization in-place to a list of Category ORM objects.

    Mutates: category.name, product.name, product.description,
             variant.name, modifier_group.name, modifier_option.name.

    Translations are expected to already be loaded via lazy="selectin"
    relationships defined in models.py.

    Args:
        categories: List of Category ORM objects (already filtered/sorted).
        lang:       Target language code ("uz", "ru", "en").
    """
    for c in categories:
        c.name = localized_name(c.translations, lang, c.name)
        for p in (c.products or []):
            p.description = localized_desc(p.translations, lang, p.description)
            p.name = localized_name(p.translations, lang, p.name)
            for v in (p.variants or []):
                v.name = localized_name(v.translations, lang, v.name)
            for g in (p.modifier_groups or []):
                g.name = localized_name(g.translations, lang, g.name)
                for o in (g.options or []):
                    o.name = localized_name(o.translations, lang, o.name)
