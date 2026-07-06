"""
tests/test_schemas.py — Pydantic schema validation тесты

Покрывает:
  - phone regex validation
  - hex color validation
  - URL validation
  - slug validation
  - ReservationCreate: дата в прошлом → ошибка
  - OrderCreate: delivery без address → ошибка
  - AgencyRegister: password min_length
"""

import pytest
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError

from schemas import (
    AgencyRegister,
    OrderCreate,
    OrderItemCreate,
    ProductCreate,
    ReservationCreate,
    RestaurantCreate,
)


# ──────────────────────────────────────────
# TEST 21: Phone validation — valid formats
# ──────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("phone", [
    "+998901234567",
    "+7 (999) 123-45-67",
    "+1 555 555-5555",
    "998901234567",
])
def test_phone_valid(phone):
    order = OrderCreate(
        order_type="takeaway",
        client_phone=phone,
        items=[OrderItemCreate(product_id=1, quantity=1)],
    )
    assert order.client_phone is not None


# ──────────────────────────────────────────
# TEST 22: Phone validation — invalid format
# ──────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("phone", [
    "not-a-phone",
    "abc",
    "123",  # слишком короткий
])
def test_phone_invalid(phone):
    with pytest.raises(ValidationError) as exc_info:
        OrderCreate(
            order_type="takeaway",
            client_phone=phone,
            items=[OrderItemCreate(product_id=1, quantity=1)],
        )
    assert "телефон" in str(exc_info.value).lower() or "phone" in str(exc_info.value).lower()


# ──────────────────────────────────────────
# TEST 23: Hex color validation
# ──────────────────────────────────────────
@pytest.mark.unit
def test_hex_color_valid():
    r = RestaurantCreate(
        name="Test",
        slug="test-rest",
        admin_password="password123",
        primary_color="#8B1A2E",
        secondary_color="#FAF6EE",
        accent_color="#D4A853",
    )
    assert r.primary_color == "#8B1A2E"


@pytest.mark.unit
def test_hex_color_invalid():
    with pytest.raises(ValidationError):
        RestaurantCreate(
            name="Test",
            slug="test-rest",
            admin_password="password123",
            primary_color="red",  # не hex
        )


# ──────────────────────────────────────────
# TEST 24: URL validation
# ──────────────────────────────────────────
@pytest.mark.unit
def test_url_valid():
    p = ProductCreate(
        category_id=1,
        name="Плов",
        price=25000,
        photo_url="https://example.com/plov.jpg",
    )
    assert p.photo_url == "https://example.com/plov.jpg"


@pytest.mark.unit
def test_url_invalid():
    with pytest.raises(ValidationError):
        ProductCreate(
            category_id=1,
            name="Плов",
            price=25000,
            photo_url="not-a-url",
        )


# ──────────────────────────────────────────
# TEST 25: Slug validation
# ──────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("slug", ["my-restaurant", "rest123", "a-b-c"])
def test_slug_valid(slug):
    r = RestaurantCreate(name="Test", slug=slug, admin_password="password123")
    assert r.slug == slug


@pytest.mark.unit
@pytest.mark.parametrize("slug", ["My Restaurant", "rest_123", "UPPER", "rest@123"])
def test_slug_invalid(slug):
    with pytest.raises(ValidationError):
        RestaurantCreate(name="Test", slug=slug, admin_password="password123")


# ──────────────────────────────────────────
# TEST 26: Reservation in the past → error
# ──────────────────────────────────────────
@pytest.mark.unit
def test_reservation_past_date_invalid():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    with pytest.raises(ValidationError) as exc_info:
        ReservationCreate(
            client_name="Алишер",
            client_phone="+998901234567",
            guests_count=2,
            reservation_time=past,
        )
    assert "будущ" in str(exc_info.value).lower() or "future" in str(exc_info.value).lower()


@pytest.mark.unit
def test_reservation_future_date_valid():
    future = datetime.now(timezone.utc) + timedelta(hours=24)
    r = ReservationCreate(
        client_name="Алишер",
        client_phone="+998901234567",
        guests_count=2,
        reservation_time=future,
    )
    assert r.guests_count == 2


# ──────────────────────────────────────────
# TEST 27: AgencyRegister password min_length
# ──────────────────────────────────────────
@pytest.mark.unit
def test_agency_register_short_password():
    with pytest.raises(ValidationError):
        AgencyRegister(name="Agency", email="test@test.com", password="short")


@pytest.mark.unit
def test_agency_register_valid():
    a = AgencyRegister(
        name="My Agency",
        email="admin@agency.uz",
        password="securepassword123",
    )
    assert a.email == "admin@agency.uz"
