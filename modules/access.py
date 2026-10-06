"""
modules/access.py — Taomly Platform

Phase 15, Slice B (R1): единая проверка доступа Restaurant → Location → Object.
Решение Owner: RD-01 = A′. Spec: PHASE_15_SLICE_B_RESERVATION_CORE_ARCHITECTURE_GAP_SPEC_v2.md, §12.

Principal — единый контекст доступа:

    restaurant      — Restaurant, от имени которого выполняется операция;
    location_scope  — набор id Location; None = все Locations этого Restaurant;
    actor_ref       — ссылка на действующего субъекта (для restaurant admin:
                      "restaurant_admin:<restaurant_id>").

Модуль НЕ вводит новую аутентификацию и новые роли. Principal строится из
Restaurant, который уже возвращает существующая зависимость
get_current_restaurant_admin (она не меняется). Модуль не обращается к БД:
Location и объект загружает вызывающий (service) — helper лишь проверяет цепочку.
Бизнес-логики броней здесь нет.

Коды ответов (существующие соглашения проекта):
    403 — чужой Restaurant (как «Нет доступа к данным этого ресторана» в роутерах);
    404 — Location не найдена или принадлежит другому Restaurant, объект не найден
          или не принадлежит этой Location («Локация не найдена»);
    403 — Location принадлежит этому Restaurant, но не входит в location_scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from models import Location, Restaurant

_DETAIL_FOREIGN_RESTAURANT = "Нет доступа к данным этого ресторана"
_DETAIL_LOCATION_NOT_FOUND = "Локация не найдена"
_DETAIL_LOCATION_OUT_OF_SCOPE = "Нет доступа к этой локации"
_DETAIL_OBJECT_NOT_FOUND = "Объект не найден"


@dataclass(frozen=True)
class Principal:
    """Контекст доступа: restaurant + location_scope + actor_ref (RD-01 = A′)."""

    restaurant: Restaurant
    location_scope: frozenset[int] | None
    actor_ref: str


def restaurant_admin_principal(restaurant: Restaurant) -> Principal:
    """
    Principal restaurant admin (RD-01 = A′): доступ ко всем Locations своего Restaurant.

    restaurant — результат существующей зависимости get_current_restaurant_admin.
    """
    return Principal(
        restaurant=restaurant,
        location_scope=None,
        actor_ref=f"restaurant_admin:{restaurant.id}",
    )


def assert_location_access(
    principal: Principal,
    *,
    restaurant_id: int,
    location: Location | None,
    obj: object | None = None,
    obj_not_found_detail: str = _DETAIL_OBJECT_NOT_FOUND,
) -> Location:
    """
    Проверяет цепочку Restaurant → Location → Object. Возвращает Location.

    1. Restaurant: principal.restaurant.id должен совпасть с restaurant_id → иначе 403.
    2. Location: должна существовать и принадлежать этому Restaurant → иначе 404
       (чужая и несуществующая Location неразличимы).
    3. Scope: если location_scope не None, Location обязана в него входить → иначе 403.
       location_scope=None даёт доступ ко всем Locations своего Restaurant.
    4. Object (необязательно): obj.restaurant_id и obj.location_id должны совпасть с
       Restaurant и Location из шагов 1–2 → иначе 404. Совпадения одного location_id
       недостаточно.
    """
    own_restaurant_id = principal.restaurant.id

    if own_restaurant_id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_DETAIL_FOREIGN_RESTAURANT,
        )

    if location is None or location.restaurant_id != own_restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_DETAIL_LOCATION_NOT_FOUND,
        )

    if principal.location_scope is not None and location.id not in principal.location_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_DETAIL_LOCATION_OUT_OF_SCOPE,
        )

    if obj is not None and (
        getattr(obj, "restaurant_id", None) != own_restaurant_id
        or getattr(obj, "location_id", None) != location.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=obj_not_found_detail,
        )

    return location
