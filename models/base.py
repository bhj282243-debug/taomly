# ──────────────────────────────────────────
# PHASE 4: MENU LOCALIZATION CONSTANTS
# ──────────────────────────────────────────
# Допустимые языки для локализации контента меню.
# НЕ импортировать из i18n.py — это отдельный domain.
# Используется в translation-моделях и write-endpoints.
MENU_LANGUAGES: frozenset = frozenset({"uz", "ru", "en"})
