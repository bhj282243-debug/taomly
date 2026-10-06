"""
tests/test_phase15_access.py — Phase 15, Slice B (R1)

Unit-тесты modules/access.py: Principal и assert_location_access (RD-01 = A′).
БД не используется: Restaurant/Location/объект — лёгкие заглушки с нужными атрибутами.
"""

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from modules.access import Principal, assert_location_access, restaurant_admin_principal


def _restaurant(rid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=rid)


def _location(lid: int, restaurant_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=lid, restaurant_id=restaurant_id)


def _obj(restaurant_id: int, location_id: int) -> SimpleNamespace:
    return SimpleNamespace(restaurant_id=restaurant_id, location_id=location_id)


def test_restaurant_admin_principal_shape():
    principal = restaurant_admin_principal(_restaurant(7))
    assert principal.location_scope is None
    assert principal.actor_ref == "restaurant_admin:7"
    assert principal.restaurant.id == 7


def test_admin_scope_none_allows_any_location_of_own_restaurant():
    principal = restaurant_admin_principal(_restaurant(1))
    for lid in (10, 11, 12):
        loc = _location(lid, restaurant_id=1)
        assert assert_location_access(principal, restaurant_id=1, location=loc) is loc


def test_admin_scope_none_denied_for_foreign_restaurant_context():
    principal = restaurant_admin_principal(_restaurant(1))
    with pytest.raises(HTTPException) as exc:
        assert_location_access(principal, restaurant_id=2, location=_location(10, 2))
    assert exc.value.status_code == 403


def test_scoped_principal_allowed_for_own_location():
    principal = Principal(_restaurant(1), frozenset({10}), "restaurant_admin:1")
    loc = _location(10)
    assert assert_location_access(principal, restaurant_id=1, location=loc) is loc


def test_scoped_principal_denied_for_other_location_of_same_restaurant():
    principal = Principal(_restaurant(1), frozenset({10}), "restaurant_admin:1")
    with pytest.raises(HTTPException) as exc:
        assert_location_access(principal, restaurant_id=1, location=_location(11))
    assert exc.value.status_code == 403


def test_empty_scope_denies_every_location():
    principal = Principal(_restaurant(1), frozenset(), "restaurant_admin:1")
    with pytest.raises(HTTPException) as exc:
        assert_location_access(principal, restaurant_id=1, location=_location(10))
    assert exc.value.status_code == 403


def test_missing_location_is_404():
    principal = restaurant_admin_principal(_restaurant(1))
    with pytest.raises(HTTPException) as exc:
        assert_location_access(principal, restaurant_id=1, location=None)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Локация не найдена"


def test_location_of_another_restaurant_is_404_like_missing():
    principal = restaurant_admin_principal(_restaurant(1))
    with pytest.raises(HTTPException) as exc:
        assert_location_access(principal, restaurant_id=1, location=_location(10, restaurant_id=2))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Локация не найдена"


def test_object_must_belong_to_restaurant_and_location():
    principal = restaurant_admin_principal(_restaurant(1))
    loc = _location(10)
    assert assert_location_access(principal, restaurant_id=1, location=loc, obj=_obj(1, 10)) is loc

    # совпадения одного location_id недостаточно: объект чужого Restaurant
    with pytest.raises(HTTPException) as foreign_restaurant:
        assert_location_access(
            principal, restaurant_id=1, location=loc, obj=_obj(2, 10),
            obj_not_found_detail="Бронь не найдена",
        )
    assert foreign_restaurant.value.status_code == 404
    assert foreign_restaurant.value.detail == "Бронь не найдена"

    # объект другой Location того же Restaurant
    with pytest.raises(HTTPException) as other_location:
        assert_location_access(principal, restaurant_id=1, location=loc, obj=_obj(1, 11))
    assert other_location.value.status_code == 404
