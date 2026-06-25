from sqlalchemy import (
    BigInteger, Boolean, Column, Float,
    ForeignKey, Integer, String, Text,
    TIMESTAMP, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Agency(Base):
    __tablename__ = "agencies"
    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False)
    owner_email = Column(String(255), unique=True, nullable=False)
    owner_password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    restaurants = relationship("Restaurant", back_populates="agency")


class Restaurant(Base):
    __tablename__ = "restaurants"
    id = Column(BigInteger, primary_key=True)
    agency_id = Column(BigInteger, ForeignKey("agencies.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text)
    phone = Column(String(50))
    address = Column(Text)
    is_active = Column(Boolean, default=True)
    is_waiter_call_enabled = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    # Auth
    admin_password_hash = Column(String(255), nullable=True)

    # White Label Branding
    logo_url = Column(Text, nullable=True)
    primary_color = Column(String(20), default="#8B1A2E")
    secondary_color = Column(String(20), default="#FAF6EE")
    accent_color = Column(String(20), default="#D4A853")
    welcome_text = Column(Text, nullable=True)
    custom_domain = Column(String(255), nullable=True, unique=True, index=True)

    # Telegram
    telegram_bot_token_encrypted = Column(Text, nullable=True)
    telegram_dispatcher_id = Column(BigInteger, nullable=True)

    agency = relationship("Agency", back_populates="restaurants")
    categories = relationship("Category", back_populates="restaurant")
    products = relationship("Product", back_populates="restaurant")
    orders = relationship("Order", back_populates="restaurant")
    reservations = relationship("Reservation", back_populates="restaurant")
    tables = relationship("RestaurantTable", back_populates="restaurant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin','owner','dispatcher','client')", name="check_user_role"),
    )
    id = Column(BigInteger, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    name = Column(String(255))
    phone = Column(String(50))
    role = Column(String(20), nullable=False, default="client")
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP, server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "name"),
    )
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, default=0)

    restaurant = relationship("Restaurant", back_populates="categories")
    products = relationship("Product", back_populates="category")


class Product(Base):
    __tablename__ = "products"
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    category_id = Column(BigInteger, ForeignKey("categories.id", ondelete="SET NULL"))
    name = Column(String(255), nullable=False)
    description = Column(Text)
    price = Column(Integer, nullable=False)
    photo_url = Column(Text)
    is_available = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    restaurant = relationship("Restaurant", back_populates="products")
    category = relationship("Category", back_populates="products")


class RestaurantTable(Base):
    __tablename__ = "restaurant_tables"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "table_number"),
    )
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    table_number = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="tables")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("order_type IN ('delivery','takeaway','dine_in')", name="check_order_type"),
        CheckConstraint("status IN ('new','accepted','preparing','ready_for_delivery','delivering','completed','cancelled')", name="check_order_status"),
    )
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    client_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    client_telegram_id = Column(BigInteger)
    client_name = Column(String(255))
    client_phone = Column(String(50))
    order_type = Column(String(20))
    address = Column(Text)
    location_lat = Column(Float)
    location_lng = Column(Float)
    table_id = Column(BigInteger, ForeignKey("restaurant_tables.id", ondelete="SET NULL"))
    comment = Column(Text)
    total_amount = Column(Integer)
    status = Column(String(20), default="new")
    created_at = Column(TIMESTAMP, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="orders")
    client = relationship("User")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_order_item_quantity"),
    )
    id = Column(BigInteger, primary_key=True)
    order_id = Column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    product_id = Column(BigInteger, ForeignKey("products.id", ondelete="SET NULL"))
    name = Column(String(255))
    price = Column(Integer)
    quantity = Column(Integer)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("status IN ('new','confirmed','completed','cancelled')", name="check_reservation_status"),
        CheckConstraint("guests_count > 0", name="check_reservation_guests"),
    )
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    client_name = Column(String(255))
    client_phone = Column(String(50))
    guests_count = Column(Integer)
    reservation_time = Column(TIMESTAMP)
    comment = Column(Text)
    status = Column(String(20), default="new")
    created_at = Column(TIMESTAMP, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="reservations")


class WaiterCall(Base):
    __tablename__ = "waiter_calls"
    __table_args__ = (
        CheckConstraint("status IN ('active','accepted','completed','cancelled')", name="check_waiter_call_status"),
    )
    id = Column(BigInteger, primary_key=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False)
    table_id = Column(BigInteger, ForeignKey("restaurant_tables.id"), nullable=False)
    status = Column(String(20), default="active")
    created_at = Column(TIMESTAMP, server_default=func.now())

    restaurant = relationship("Restaurant")
    table = relationship("RestaurantTable")
