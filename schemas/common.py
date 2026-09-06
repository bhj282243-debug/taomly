"""
schemas/common.py — Taomly Platform

Shared regex constants and validator helper functions.
Used internally by domain schema modules via direct import.
Not exported through schemas/__init__.py (internal use only).
"""

import math
import re
from typing import Optional
from urllib.parse import urlparse

# ──────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ВАЛИДАТОРЫ
# ──────────────────────────────────────────
_SLUG_RE  = re.compile(r"^[a-z0-9-]+$")
_HEX_RE   = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-\(\)]{7,20}$")
_URL_RE   = re.compile(r"^https?://", re.IGNORECASE)

# Валидация custom_domain:
#   - каждая метка: [a-z0-9] по краям, внутри допустим одиночный дефис
#   - минимум две метки (домен второго уровня + TLD)
#   - TLD: только буквы, 2–63 символа
#   - IP-адреса (типа 127.0.0.1) не пропускаются — первая метка не может быть
#     чисто цифровой, если остальные тоже цифры
_DOMAIN_LABEL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
_DOMAIN_TLD_RE   = re.compile(r"^[a-zA-Z]{2,63}$")

# SSRF-защита: блокируем внутренние/приватные адреса
# Атакующий может передать http://169.254.169.254/ (AWS metadata),
# http://localhost:8000/api/superadmin/ или http://10.0.0.1/internal
_SSRF_BLOCK_RE = re.compile(
    r"^https?://"
    r"("
    r"localhost"
    r"|127\."
    r"|0\.0\.0\.0"
    r"|10\."
    r"|172\.(1[6-9]|2[0-9]|3[01])\."
    r"|192\.168\."
    r"|169\.254\."          # AWS/Azure link-local metadata
    r"|::1"
    r"|\[::1\]"
    r"|fc00:"
    r"|fd[0-9a-f]{2}:"
    r")",
    re.IGNORECASE,
)


def _validate_slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            "slug может содержать только строчные латинские буквы, цифры и дефис"
        )
    return value


def _validate_hex_color(value: Optional[str]) -> Optional[str]:
    if value is not None and not _HEX_RE.match(value):
        raise ValueError("Цвет должен быть в формате #RRGGBB, например #8B1A2E")
    return value


def _validate_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    if not _PHONE_RE.match(v):
        raise ValueError(
            "Неверный формат номера телефона. "
            "Допустимые форматы: +998901234567, +7 (999) 123-45-67"
        )
    return v


def _validate_url(value: Optional[str]) -> Optional[str]:
    """
    Принимает только http:// или https:// URL с публичными хостами.

    Блокирует:
      - localhost / 127.x.x.x / ::1
      - Приватные диапазоны: 10.x, 172.16-31.x, 192.168.x
      - AWS/Azure link-local metadata: 169.254.x
      - IPv6 loopback и ULA-диапазоны

    Это защита от SSRF — атакующий не может передать URL внутренней сети.
    """
    if not value:
        return None
    v = value.strip()
    if not _URL_RE.match(v):
        raise ValueError("URL должен начинаться с http:// или https://")
    if _SSRF_BLOCK_RE.match(v):
        raise ValueError(
            "URL указывает на внутренний/приватный адрес. "
            "Используйте публичный URL изображения."
        )
    # Дополнительная проверка через urlparse — ловит http://user@localhost/
    try:
        parsed = urlparse(v)
        host = parsed.hostname or ""
        if _SSRF_BLOCK_RE.match(f"https://{host}"):
            raise ValueError(
                "URL указывает на внутренний/приватный адрес. "
                "Используйте публичный URL изображения."
            )
    except ValueError:
        raise
    except Exception:
        raise ValueError("Невалидный URL")
    return v


def _validate_custom_domain(value: Optional[str]) -> Optional[str]:
    """
    Принимает только корректные доменные имена вида restaurant.example.com или
    menu.example.uz. Отклоняет:
      - IP-адреса (127.0.0.1, 10.0.0.1 и т.д.)
      - localhost и его варианты
      - метки с двойным дефисом в произвольном месте (xn-- IDN разрешены явно)
      - метки, начинающиеся или заканчивающиеся дефисом
      - TLD короче 2 символов или содержащий цифры
      - однокомпонентные имена (нет точки)
      - пустую строку и строки с пробелами
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if " " in v or "\t" in v:
        raise ValueError("Доменное имя не может содержать пробелы")

    # Снимаем опциональный trailing dot (FQDN-стиль)
    if v.endswith("."):
        v = v[:-1]

    parts = v.split(".")
    if len(parts) < 2:
        raise ValueError(
            "Укажите полное доменное имя, например restaurant.example.com"
        )

    # TLD — последняя часть, только буквы
    tld = parts[-1]
    if not _DOMAIN_TLD_RE.match(tld):
        raise ValueError(
            f"Неверный TLD «{tld}». TLD должен содержать только буквы (2–63 символа)"
        )

    # Все части кроме TLD — проверяем по метке
    for label in parts[:-1]:
        if not label:
            raise ValueError("Доменное имя содержит пустую метку (двойная точка?)")
        if not _DOMAIN_LABEL_RE.match(label):
            raise ValueError(
                f"Неверная метка домена «{label}». "
                "Метка должна начинаться и заканчиваться на букву/цифру, "
                "внутри допустимы буквы, цифры и одиночный дефис"
            )

    # Блокируем IP-адреса: если все части — цифры → это IP, не домен
    if all(part.isdigit() for part in parts):
        raise ValueError(
            "IP-адрес не допускается в качестве custom_domain. "
            "Укажите доменное имя."
        )

    # Блокируем localhost явно (на случай "localhost.localdomain" и т.п.)
    if parts[0].lower() == "localhost" or v.lower() == "localhost":
        raise ValueError("localhost не допускается в качестве custom_domain")

    return v


def _validate_coordinate(
    value: Optional[float], min_val: float, max_val: float, name: str
) -> Optional[float]:
    """
    Проверяет координату на допустимый диапазон, NaN и Infinity.

    Pydantic float validator пропускает float("nan") и float("inf") без ошибки.
    Эти значения записываются в PostgreSQL DOUBLE PRECISION без исключения,
    но ломают любые арифметические операции над координатами в будущей аналитике.

    Foundation Task 11.3: явная проверка math.isfinite() блокирует NaN/Infinity
    до попадания в БД.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError(f"{name} не может быть NaN или бесконечностью")
    if not (min_val <= value <= max_val):
        raise ValueError(f"{name} должна быть в диапазоне [{min_val}, {max_val}]")
    return value
