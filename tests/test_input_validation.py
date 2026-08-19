"""
tests/test_input_validation.py — Foundation Task 10: Input Validation & Data Integrity

Покрывает:
  F-1/F-2 (уже исправлены ранее):
    IV-01. OrderItemCreate.quantity: 0 → ValidationError
    IV-02. OrderItemCreate.quantity: 1 → OK
    IV-03. OrderItemCreate.quantity: 99 → OK (текущий le=99)
    IV-04. OrderItemCreate.quantity: 100 → ValidationError
    IV-05. OrderItemCreate.product_id: -1 → ValidationError
    IV-06. OrderItemCreate.product_id: 0 → ValidationError
    IV-07. OrderItemCreate.product_id: 1 → OK

  F-3:
    IV-08. RestaurantSettingsUpdate.working_hours: 50 символов → OK
    IV-09. RestaurantSettingsUpdate.working_hours: 51 символов → ValidationError

  F-4 (уже исправлены ранее):
    IV-10. ProductCreate.name: 255 символов → OK
    IV-11. ProductCreate.name: 256 символов → ValidationError
    IV-12. CategoryCreate.name: 255 символов → OK
    IV-13. CategoryCreate.name: 256 символов → ValidationError
    IV-14. ProductUpdate.name: 255 символов → OK
    IV-15. ProductUpdate.name: 256 символов → ValidationError
    IV-16. CategoryUpdate.name: 255 символов → OK
    IV-17. CategoryUpdate.name: 256 символов → ValidationError

  F-5 (уже исправлена ранее):
    IV-18. ReservationCreate.comment: 500 символов → OK
    IV-19. ReservationCreate.comment: 501 символов → ValidationError

  F-6:
    IV-20. TableCreateRequest: "1" → OK
    IV-21. TableCreateRequest: "VIP" → OK
    IV-22. TableCreateRequest: "T-DUPLICATE-TEST" → OK (дефис допустим)
    IV-23. TableCreateRequest: "Стол 5" → OK (кириллица + пробел)
    IV-24. TableCreateRequest: "<script>" → ValidationError
    IV-25. TableCreateRequest: "'; DROP" → ValidationError
    IV-26. TableCreateRequest: пустая строка → ValidationError
    IV-27. TableCreateRequest: "   " → ValidationError (только пробелы → пустая после strip)

  F-7 (integration):
    IV-28. PATCH /me/settings с working_hours=51 символ → 422
    IV-29. PATCH /me/settings с working_hours=50 символов → 200
    IV-30. PATCH /me/settings с пустым телом → 200 (не ломает текущие данные)

  Backward compatibility (regression):
    IV-31. POST /api/orders/ с quantity=1 → 201 (не сломали заказы)
    IV-32. POST /api/restaurants/me/tables с "5" → 201 (существующий формат OK)
    IV-33. POST /api/restaurants/me/tables с "VIP" → 201 (буквы OK)
"""

import pytest
from pydantic import ValidationError

from schemas import (
    CategoryCreate,
    CategoryUpdate,
    OrderItemCreate,
    ProductCreate,
    ProductUpdate,
    ReservationCreate,
    TableCreateRequest,
)


# ═══════════════════════════════════════════════════════════════════
# F-1/F-2: OrderItemCreate — quantity и product_id
# ═══════════════════════════════════════════════════════════════════

class TestOrderItemCreate:

    def test_iv01_quantity_zero_rejected(self):
        """IV-01: quantity=0 → ValidationError (ge=1)."""
        with pytest.raises(ValidationError):
            OrderItemCreate(product_id=1, quantity=0)

    def test_iv02_quantity_one_accepted(self):
        """IV-02: quantity=1 → OK."""
        item = OrderItemCreate(product_id=1, quantity=1)
        assert item.quantity == 1

    def test_iv03_quantity_at_limit_accepted(self):
        """IV-03: quantity=99 → OK (le=99)."""
        item = OrderItemCreate(product_id=1, quantity=99)
        assert item.quantity == 99

    def test_iv04_quantity_over_limit_rejected(self):
        """IV-04: quantity=100 → ValidationError (le=99)."""
        with pytest.raises(ValidationError):
            OrderItemCreate(product_id=1, quantity=100)

    def test_iv05_product_id_negative_rejected(self):
        """IV-05: product_id=-1 → ValidationError (gt=0)."""
        with pytest.raises(ValidationError):
            OrderItemCreate(product_id=-1, quantity=1)

    def test_iv06_product_id_zero_rejected(self):
        """IV-06: product_id=0 → ValidationError (gt=0)."""
        with pytest.raises(ValidationError):
            OrderItemCreate(product_id=0, quantity=1)

    def test_iv07_product_id_positive_accepted(self):
        """IV-07: product_id=1 → OK."""
        item = OrderItemCreate(product_id=1, quantity=1)
        assert item.product_id == 1


# ═══════════════════════════════════════════════════════════════════
# F-3: RestaurantSettingsUpdate.working_hours — max_length=50
# ═══════════════════════════════════════════════════════════════════

class TestRestaurantSettingsUpdateWorkingHours:

    def test_iv08_working_hours_50_chars_accepted(self):
        """IV-08: working_hours ровно 50 символов → OK."""
        from routers.restaurants import RestaurantSettingsUpdate
        v = "A" * 50
        obj = RestaurantSettingsUpdate(working_hours=v)
        assert obj.working_hours == v

    def test_iv09_working_hours_51_chars_rejected(self):
        """IV-09: working_hours 51 символ → ValidationError."""
        from routers.restaurants import RestaurantSettingsUpdate
        with pytest.raises(ValidationError):
            RestaurantSettingsUpdate(working_hours="A" * 51)

    def test_iv08b_working_hours_none_accepted(self):
        """IV-08b: working_hours=None → OK (optional поле)."""
        from routers.restaurants import RestaurantSettingsUpdate
        obj = RestaurantSettingsUpdate(working_hours=None)
        assert obj.working_hours is None

    def test_iv08c_working_hours_typical_value_accepted(self):
        """IV-08c: типичное значение '10:00-22:00' → OK."""
        from routers.restaurants import RestaurantSettingsUpdate
        obj = RestaurantSettingsUpdate(working_hours="10:00-22:00")
        assert obj.working_hours == "10:00-22:00"


# ═══════════════════════════════════════════════════════════════════
# F-4: Product/Category name — max_length=255
# ═══════════════════════════════════════════════════════════════════

class TestProductCategoryNameLength:

    def test_iv10_product_name_255_accepted(self):
        """IV-10: ProductCreate.name 255 символов → OK."""
        p = ProductCreate(category_id=1, name="А" * 255, price=1000)
        assert len(p.name) == 255

    def test_iv11_product_name_256_rejected(self):
        """IV-11: ProductCreate.name 256 символов → ValidationError."""
        with pytest.raises(ValidationError):
            ProductCreate(category_id=1, name="А" * 256, price=1000)

    def test_iv12_category_name_255_accepted(self):
        """IV-12: CategoryCreate.name 255 символов → OK."""
        c = CategoryCreate(name="А" * 255)
        assert len(c.name) == 255

    def test_iv13_category_name_256_rejected(self):
        """IV-13: CategoryCreate.name 256 символов → ValidationError."""
        with pytest.raises(ValidationError):
            CategoryCreate(name="А" * 256)

    def test_iv14_product_update_name_255_accepted(self):
        """IV-14: ProductUpdate.name 255 символов → OK."""
        p = ProductUpdate(name="Б" * 255)
        assert p.name is not None

    def test_iv15_product_update_name_256_rejected(self):
        """IV-15: ProductUpdate.name 256 символов → ValidationError."""
        with pytest.raises(ValidationError):
            ProductUpdate(name="Б" * 256)

    def test_iv16_category_update_name_255_accepted(self):
        """IV-16: CategoryUpdate.name 255 символов → OK."""
        c = CategoryUpdate(name="В" * 255)
        assert c.name is not None

    def test_iv17_category_update_name_256_rejected(self):
        """IV-17: CategoryUpdate.name 256 символов → ValidationError."""
        with pytest.raises(ValidationError):
            CategoryUpdate(name="В" * 256)


# ═══════════════════════════════════════════════════════════════════
# F-5: ReservationCreate.comment — max_length=500
# ═══════════════════════════════════════════════════════════════════

class TestReservationComment:

    def _base_reservation(self) -> dict:
        from datetime import datetime, timedelta, timezone
        return {
            "client_name": "Тест",
            "client_phone": "+998901234567",
            "guests_count": 2,
            "reservation_time": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        }

    def test_iv18_comment_500_chars_accepted(self):
        """IV-18: ReservationCreate.comment 500 символов → OK."""
        data = self._base_reservation()
        data["comment"] = "Г" * 500
        r = ReservationCreate(**data)
        assert len(r.comment) == 500

    def test_iv19_comment_501_chars_rejected(self):
        """IV-19: ReservationCreate.comment 501 символ → ValidationError."""
        data = self._base_reservation()
        data["comment"] = "Г" * 501
        with pytest.raises(ValidationError):
            ReservationCreate(**data)

    def test_iv18b_comment_none_accepted(self):
        """IV-18b: comment=None → OK (optional)."""
        data = self._base_reservation()
        r = ReservationCreate(**data)
        assert r.comment is None


# ═══════════════════════════════════════════════════════════════════
# F-6: TableCreateRequest — allowlist validation
# ═══════════════════════════════════════════════════════════════════

class TestTableNumberValidation:

    def test_iv20_digit_accepted(self):
        """IV-20: table_number='1' → OK."""
        t = TableCreateRequest(table_number="1")
        assert t.table_number == "1"

    def test_iv21_uppercase_accepted(self):
        """IV-21: table_number='VIP' → OK."""
        t = TableCreateRequest(table_number="VIP")
        assert t.table_number == "VIP"

    def test_iv22_hyphen_accepted(self):
        """IV-22: 'T-DUPLICATE-TEST' → OK (дефис допустим)."""
        t = TableCreateRequest(table_number="T-DUPLICATE-TEST")
        assert t.table_number == "T-DUPLICATE-TEST"

    def test_iv23_cyrillic_and_space_accepted(self):
        """IV-23: 'Стол 5' → OK (кириллица + пробел)."""
        t = TableCreateRequest(table_number="Стол 5")
        assert t.table_number == "Стол 5"

    def test_iv23b_strip_applied(self):
        """IV-23b: ведущие/trailing пробелы убираются."""
        t = TableCreateRequest(table_number="  5  ")
        assert t.table_number == "5"

    def test_iv24_script_tag_rejected(self):
        """IV-24: '<script>' → ValidationError (XSS prevention)."""
        with pytest.raises(ValidationError):
            TableCreateRequest(table_number="<script>")

    def test_iv25_sql_injection_rejected(self):
        """IV-25: \"'; DROP\" → ValidationError."""
        with pytest.raises(ValidationError):
            TableCreateRequest(table_number="'; DROP TABLE")

    def test_iv26_empty_string_rejected(self):
        """IV-26: пустая строка → ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            TableCreateRequest(table_number="")

    def test_iv27_only_spaces_rejected(self):
        """IV-27: '   ' → ValidationError (пустая после strip)."""
        with pytest.raises(ValidationError):
            TableCreateRequest(table_number="   ")

    def test_iv27b_underscore_accepted(self):
        """IV-27b: 'TABLE_1' → OK (underscore допустим)."""
        t = TableCreateRequest(table_number="TABLE_1")
        assert t.table_number == "TABLE_1"


# ═══════════════════════════════════════════════════════════════════
# F-7 + Backward compatibility: Integration tests (через HTTP client)
# ═══════════════════════════════════════════════════════════════════

class TestSettingsEndpointIntegration:

    def test_iv28_working_hours_51_returns_422(self, client):
        """IV-28: PATCH /me/settings с working_hours=51 → 422."""
        resp = client.patch(
            "/api/restaurants/me/settings",
            json={"working_hours": "A" * 51},
        )
        assert resp.status_code == 422

    def test_iv29_working_hours_50_returns_200(self, client):
        """IV-29: PATCH /me/settings с working_hours=50 → 200."""
        resp = client.patch(
            "/api/restaurants/me/settings",
            json={"working_hours": "A" * 50},
        )
        assert resp.status_code == 200

    def test_iv30_empty_body_returns_200(self, client):
        """IV-30: PATCH /me/settings с {} → 200 (ничего не меняет)."""
        resp = client.patch("/api/restaurants/me/settings", json={})
        assert resp.status_code == 200

    def test_iv30b_typical_working_hours_accepted(self, client):
        """IV-30b: типичное значение '10:00-22:00' → 200."""
        resp = client.patch(
            "/api/restaurants/me/settings",
            json={"working_hours": "10:00-22:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["working_hours"] == "10:00-22:00"


class TestTablesEndpointIntegration:

    def test_iv32_digit_table_created(self, client):
        """IV-32: POST /me/tables с '5' → 201 (существующий формат OK)."""
        resp = client.post(
            "/api/restaurants/me/tables",
            json={"table_number": "5"},
        )
        assert resp.status_code == 201

    def test_iv33_vip_table_created(self, client):
        """IV-33: POST /me/tables с 'VIP' → 201."""
        resp = client.post(
            "/api/restaurants/me/tables",
            json={"table_number": "VIP"},
        )
        assert resp.status_code == 201

    def test_iv33b_hyphen_table_created(self, client):
        """IV-33b: POST /me/tables с 'A-1' → 201 (дефис OK)."""
        resp = client.post(
            "/api/restaurants/me/tables",
            json={"table_number": "A-1"},
        )
        assert resp.status_code == 201

    def test_iv33c_script_table_rejected(self, client):
        """IV-33c: POST /me/tables с '<script>' → 422."""
        resp = client.post(
            "/api/restaurants/me/tables",
            json={"table_number": "<script>alert(1)</script>"},
        )
        assert resp.status_code == 422


class TestOrderCreationRegression:

    def test_iv31_order_with_valid_quantity(self, client, product):
        """IV-31: POST /api/orders/ с quantity=1 → 201 (не сломали заказы)."""
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "client_name": "Тест",
                "client_phone": "+998901234567",
                "items": [{"product_id": product.id, "quantity": 1}],
            },
        )
        assert resp.status_code == 201

    def test_iv31b_order_quantity_over_limit_rejected(self, client, product):
        """IV-31b: quantity=100 в /api/orders/ → 422."""
        resp = client.post(
            "/api/orders/",
            json={
                "order_type": "takeaway",
                "items": [{"product_id": product.id, "quantity": 100}],
            },
        )
        assert resp.status_code == 422
