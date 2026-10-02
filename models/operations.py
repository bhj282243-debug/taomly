"""
models/operations.py — Taomly Platform
Operations: RestaurantTable, Reservation, WaiterCall.
"""

from sqlalchemy import (
    BigInteger, Column, CheckConstraint, ForeignKey,
    Index, Integer, String, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from database import Base


def phone_digits(value) -> str:
    """
    Телефон → только цифры 0-9 (Phase 15, MC-07).

    Других преобразований нет (например, код страны не добавляется):
    '+998 (90) 123-45-67' → '998901234567'. ASCII-диапазон явный, чтобы
    результат совпадал с SQL backfill в миграции 0030 (regexp_replace '[^0-9]').
    """
    return "".join(ch for ch in (value or "") if "0" <= ch <= "9")


# ──────────────────────────────────────────
# RESTAURANT TABLE
# ──────────────────────────────────────────
class RestaurantTable(Base):
    __tablename__ = "restaurant_tables"
    __table_args__ = (
        # S1-2: unique table_number within a Location.
        # Allows same table_number across different Locations of the same Brand.
        # uq_table_restaurant_number dropped in migration 0011.
        UniqueConstraint("location_id", "table_number", name="uq_table_location_number"),
    )

    id            = Column(BigInteger, primary_key=True)
    # Legacy: restaurant_id will be removed in migration 0015.
    # Kept for backward compat with all existing queries.
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S1-2: location_id — new tenant identity for tables.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_number = Column(String(50), nullable=False)
    created_at   = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    restaurant = relationship("Restaurant", back_populates="tables", lazy="select")
    # S1-2: location relationship — for future use; does not break existing code.
    location   = relationship("Location", lazy="select")

    def __repr__(self) -> str:
        return f"<RestaurantTable id={self.id} number={self.table_number!r}>"


# ──────────────────────────────────────────
# RESERVATION
# ──────────────────────────────────────────
class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','confirmed','completed','cancelled')",
            name="check_reservation_status",
        ),
        CheckConstraint("guests_count > 0", name="check_reservation_guests"),
        Index("ix_reservations_restaurant_time", "restaurant_id", "reservation_time"),
        # Phase 15 (MC-07): подсчёт гостевых лимитов по паре (телефон + Location).
        Index(
            "ix_reservations_location_phone_created",
            "location_id", "client_phone_digits", "created_at",
        ),
    )

    id               = Column(BigInteger, primary_key=True)
    restaurant_id    = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S1-4: Location-level tenant scope.
    # ON DELETE RESTRICT: бронь — исторический документ; Location с бронями
    # физически удалить нельзя. Soft delete (is_active=False) — единственный
    # допустимый способ деактивации.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    client_name      = Column(String(255), nullable=False)
    client_phone     = Column(String(50), nullable=False)
    # Phase 15 (MC-07): digits-only представление client_phone для подсчёта лимитов в БД.
    # NULL допустим только у строк, созданных до деплоя (их добирает миграция 0031).
    client_phone_digits = Column(String(50), nullable=True)
    guests_count     = Column(Integer, nullable=False)
    reservation_time = Column(TIMESTAMP(timezone=True), nullable=False)
    comment          = Column(Text)
    status           = Column(String(20), default="new", nullable=False)
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", back_populates="reservations", lazy="select")
    location   = relationship("Location", lazy="select")

    @validates("client_phone")
    def _sync_phone_digits(self, key, value):
        """
        Любая запись client_phone автоматически обновляет client_phone_digits.
        Новая бронь (из API, сервиса или фикстуры) не может появиться без
        корректного digits-значения (Phase 15, MC-07).
        """
        self.client_phone_digits = phone_digits(value)
        return value

    def __repr__(self) -> str:
        return f"<Reservation id{self.id} client={self.client_name!r} status={self.status!r}>"


# ──────────────────────────────────────────
# WAITER CALL
# ──────────────────────────────────────────
class WaiterCall(Base):
    __tablename__ = "waiter_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','accepted','completed','cancelled')",
            name="check_waiter_call_status",
        ),
        Index("ix_waiter_calls_restaurant_status", "restaurant_id", "status"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S1-4: Location-level tenant scope.
    # ON DELETE CASCADE: вызов официанта — оперативная запись.
    # При удалении Location вызовы удаляются вместе с ней.
    location_id = Column(
        BigInteger,
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_id   = Column(
        BigInteger,
        ForeignKey("restaurant_tables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status     = Column(String(20), default="active", nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", lazy="select")
    location   = relationship("Location", lazy="select")
    table      = relationship("RestaurantTable", lazy="select")

    def __repr__(self) -> str:
        return f"<WaiterCall id={self.id} table_id={self.table_id} status={self.status!r}>"
