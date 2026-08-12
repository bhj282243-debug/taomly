"""
i18n.py — Taomly Translation Engine

Минимальный translation engine для UZ / RU / EN.

Использование:
    from i18n import t

    t("common.loading", "uz")           → "Yuklanmoqda..."
    t("order.number", "en", id=42)      → "Order #42"
    t("unknown.key", "ru")              → "unknown.key"  (fallback к ключу)

Архитектура:
    - JSON-файлы загружаются с диска один раз (lazy load + in-memory cache).
    - Interpolation: {{var}} синтаксис, заменяется через **kwargs.
    - Fallback цепочка: запрошенный язык → en → uz → сам ключ.
    - Неизвестный язык нормализуется до "uz".
    - Нет внешних зависимостей.

Translation files:
    i18n/uz.json
    i18n/ru.json
    i18n/en.json
"""

import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# КОНСТАНТЫ
# ──────────────────────────────────────────

SUPPORTED_LANGUAGES = {"uz", "ru", "en"}
DEFAULT_LANGUAGE = "uz"

# Путь к папке с JSON-файлами.
# Резолвим относительно этого файла — работает и при запуске из корня,
# и при импорте из тестов в другом cwd.
_I18N_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")

# In-memory cache: {"uz": {...}, "ru": {...}, "en": {...}}
# Заполняется лениво при первом обращении к каждому языку.
_cache: Dict[str, Dict[str, str]] = {}

# Regex для поиска placeholder-ов вида {{var}}
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


# ──────────────────────────────────────────
# ВНУТРЕННИЕ ФУНКЦИИ
# ──────────────────────────────────────────

def _load(lang: str) -> Dict[str, str]:
    """
    Загружает JSON-файл для указанного языка и кладёт в кеш.
    При ошибке чтения возвращает пустой словарь и логирует предупреждение.
    Вызывается только один раз на язык.
    """
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.error("i18n: %s содержит не объект JSON", path)
            return {}
        logger.debug("i18n: загружен %s (%d ключей)", path, len(data))
        return data
    except FileNotFoundError:
        logger.warning("i18n: файл не найден: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("i18n: ошибка парсинга %s: %s", path, exc)
        return {}


def _get_translations(lang: str) -> Dict[str, str]:
    """
    Возвращает словарь переводов для языка из кеша.
    Если ещё не загружен — загружает и кеширует.
    """
    if lang not in _cache:
        _cache[lang] = _load(lang)
    return _cache[lang]


def _normalize_lang(lang: str) -> str:
    """
    Нормализует код языка.
    Неизвестный язык → DEFAULT_LANGUAGE ("uz").
    """
    normalized = lang.strip().lower() if lang else DEFAULT_LANGUAGE
    if normalized not in SUPPORTED_LANGUAGES:
        logger.debug("i18n: неизвестный язык %r → %s", lang, DEFAULT_LANGUAGE)
        return DEFAULT_LANGUAGE
    return normalized


def _interpolate(template: str, kwargs: dict) -> str:
    """
    Заменяет {{var}} на значения из kwargs.
    Неизвестный placeholder оставляет как есть.
    """
    if not kwargs:
        return template

    def replace(match: re.Match) -> str:
        key = match.group(1)
        return str(kwargs[key]) if key in kwargs else match.group(0)

    return _PLACEHOLDER_RE.sub(replace, template)


# ──────────────────────────────────────────
# ПУБЛИЧНЫЙ API
# ──────────────────────────────────────────

def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Возвращает перевод строки по ключу для указанного языка.

    Fallback цепочка:
        1. Запрошенный язык
        2. "en"
        3. "uz"
        4. Сам key (никогда не возвращает пустую строку)

    Interpolation:
        t("order.number", "en", id=42)  → "Order #42"
        t("validation.minimum_order", "ru", amount="50 000 so'm")

    Args:
        key:    Ключ перевода, например "common.loading" или "order.number".
        lang:   Код языка: "uz", "ru", "en". Неизвестный → "uz".
        **kwargs: Переменные для interpolation {{var}}.

    Returns:
        Переведённая строка с подставленными переменными.
        Никогда не возвращает None или пустую строку (если key не пустой).
    """
    normalized = _normalize_lang(lang)

    # Fallback цепочка
    fallback_chain = [normalized]
    if "en" not in fallback_chain:
        fallback_chain.append("en")
    if "uz" not in fallback_chain:
        fallback_chain.append("uz")

    for candidate in fallback_chain:
        translations = _get_translations(candidate)
        if key in translations:
            return _interpolate(translations[key], kwargs)

    # Последний fallback — сам ключ
    logger.warning("i18n: ключ %r не найден ни в одном языке", key)
    return key


def get_keys(lang: str = DEFAULT_LANGUAGE) -> set:
    """
    Возвращает множество всех ключей для языка.
    Используется в тестах для проверки полноты словарей.
    """
    normalized = _normalize_lang(lang)
    return set(_get_translations(normalized).keys())


def clear_cache() -> None:
    """
    Очищает in-memory кеш.
    Используется в тестах для изоляции.
    """
    _cache.clear()
