"""
tests/conftest.py — Taomly Platform
Pytest fixtures: test DB (SQLite local / PostgreSQL CI), test client, pre-seeded data.

Исправление v2 (Foundation Task 1):
  - Восстановлена фикстура category() — тело случайно попало в restaurant_rub после return r

Исправление v3 (Foundation Task 8):
  - DATABASE_URL из окружения теперь реально используется для выбора тестовой БД.
  - Если DATABASE_URL указывает на PostgreSQL — engine создаётся для PostgreSQL
    (без SQLite-специфичных connect_args и PRAGMA foreign_keys).
  - Если DATABASE_URL отсутствует или SQLite — прежнее поведение (in-memory SQLite).
  - create_tables fixture пропускает Base.metadata.create_all для PostgreSQL
    (таблицы уже созданы Alembic-миграциями в CI).
  - _is_sqlite() и pytest_collection_modifyitems корректно работают для обоих диалектов.
"""

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-32-chars-here-ok")
os.environ.setdefault("FERNET_KEY", "bQG9o6ru5yC7KSDNJaHlTYr4YEyAp7SDUAYZQQmygX4=")
os.environ.setdefault("WEBHOOK_URL", "https://test.taomly.uz")
os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("SUPERADMIN_EMAIL", "test-admin@example.com")
os.environ.setdefault("SUPERADMIN_PASSWORD", "test-superadmin-password")
# ENVIRONMENT не задаём — дефолт "development" позволяет WEBHOOK_SECRET
# работать без явного значения (детерминированный fallback из _load_webhook_secret).

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from auth import (
    TelegramUser,
    create_agency_token,
    create_restaurant_token,
    encrypt_token,
    get_current_agency,
    get_current_restaurant_admin,
    get_telegram_user,
    hash_password,
)
from database import Base, get_db

_FERNET_KEY = os.environ["FERNET_KEY"]
_fernet_test = Fernet(_FERNET_KEY.encode())


def _encrypt(token: str) -> str:
    return _fernet_test.encrypt(token.encode()).decode()


# ──────────────────────────────────────────
# DATABASE ENGINE
# Читаем DATABASE_URL из окружения.
# PostgreSQL CI: DATABASE_URL=postgresql://taomly:testpass@localhost:5432/taomly_test
# Локально без переменной: fallback на SQLite in-memory.
# ──────────────────────────────────────────
SQLALCHEMY_TEST_URL = os.environ["DATABASE_URL"]

_is_postgres = SQLALCHEMY_TEST_URL.startswith("postgresql")

if _is_postgres:
    # PostgreSQL: без SQLite-специфичных аргументов
    engine = create_engine(SQLALCHEMY_TEST_URL)
else:
    # SQLite: нужен check_same_thread=False и PRAGMA foreign_keys
    engine = create_engine(
        SQLALCHEMY_TEST_URL,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """
    SQLite: создаёт таблицы через Base.metadata.create_all — нет Alembic.
    PostgreSQL: таблицы уже созданы `alembic upgrade head` в CI — пропускаем create_all,
    чтобы не конфликтовать с миграциями.
    """
    import models  # noqa: F401

    if not _is_postgres:
        Base.metadata.create_all(bind=engine)
    yield
    if not _is_postgres:
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """
    Изолированная БД-сессия на один тест.
    После теста — rollback, данные не остаются.

    SQLAlchemy 2.0: subtransactions удалены. begin_nested() создаёт SAVEPOINT.
    session.commit() внутри теста коммитит до SAVEPOINT, внешний rollback
    откатывает всё. Изоляция между тестами сохраняется.

    Работает одинаково для SQLite и PostgreSQL.
    """
    connection = engine.connect()
    transaction = connection.begin()
    connection.begin_nested()  # SAVEPOINT
    session = TestingSessionLocal(bind=connection)

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        if transaction.nested and not transaction._parent.nested:
            session.expire_all()
            connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ──────────────────────────────────────────
# MODELS
# ──────────────────────────────────────────
from models import (  # noqa: E402
    Agency,
    Category,
    Order,
    OrderItem,
    Product,
    Reservation,
    Restaurant,
    RestaurantTable,
    WaiterCall,
)


# ──────────────────────────────────────────
# DATA FIXTURES
# ──────────────────────────────────────────
@pytest.fixture
def agency(db) -> Agency:
    a = Agency(
        name="Test Agency",
        owner_email="test@agency.uz",
        owner_password_hash=hash_password("password123"),
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture
def agency2(db) -> Agency:
    """Второе агентство — для тестов tenant isolation."""
    a = Agency(
        name="Other Agency",
        owner_email="other@agency.uz",
        owner_password_hash=hash_password("password123"),
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture
def restaurant(db, agency) -> Restaurant:
    r = Restaurant(
        agency_id=agency.id,
        name="Chinar Restaurant",
        slug="chinar",
        admin_password_hash=hash_password("secret"),
        primary_color="#8B1A2E",
        secondary_color="#FAF6EE",
        accent_color="#D4A853",
        telegram_bot_token_encrypted=_encrypt("1234567890:AAFakeTokenForTests"),
        telegram_dispatcher_id=12345678,
        currency="UZS",
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def restaurant2(db, agency2) -> Restaurant:
    """Второй ресторан другого агентства — для тестов tenant isolation."""
    r = Restaurant(
        agency_id=agency2.id,
        name="Palace Restaurant",
        slug="palace",
        admin_password_hash=hash_password("secret"),
        primary_color="#1A1A2E",
        secondary_color="#F0F0F0",
        accent_color="#C0A060",
        telegram_bot_token_encrypted=_encrypt("9876543210:AAFakeTokenForTests2"),
        telegram_dispatcher_id=87654321,
        currency="USD",
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def restaurant_rub(db, agency) -> Restaurant:
    """Ресторан с валютой RUB — для тестов currency formatter."""
    r = Restaurant(
        agency_id=agency.id,
        name="Moscow Restaurant",
        slug="moscow",
        admin_password_hash=hash_password("secret"),
        primary_color="#1A1A2E",
        secondary_color="#F0F0F0",
        accent_color="#C0A060",
        telegram_bot_token_encrypted=_encrypt("1111111111:AAFakeTokenRUB"),
        telegram_dispatcher_id=11111111,
        currency="RUB",
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def category(db, restaurant) -> Category:
    """Категория меню для основного тестового ресторана."""
    c = Category(restaurant_id=restaurant.id, name="Горячие блюда", sort_order=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def product(db, restaurant, category) -> Product:
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Самса",
        price=15000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def product2(db, restaurant, category) -> Product:
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Лагман",
        price=30000,
        is_available=True,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def product_unavailable(db, restaurant, category) -> Product:
    p = Product(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name="Недоступное блюдо",
        price=10000,
        is_available=False,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def table(db, restaurant) -> RestaurantTable:
    t = RestaurantTable(restaurant_id=restaurant.id, table_number="5")
    db.add(t)
    db.flush()
    return t


@pytest.fixture
def tg_user(restaurant) -> TelegramUser:
    """Верифицированный Telegram-пользователь ресторана."""
    return TelegramUser(
        id=111111111,
        first_name="Алишер",
        last_name="Навоий",
        username="alisher",
        language_code="uz",
        restaurant_id=restaurant.id,
        restaurant=restaurant,
    )


@pytest.fixture
def tg_user2(restaurant2) -> TelegramUser:
    """Пользователь второго ресторана — для тестов tenant isolation."""
    return TelegramUser(
        id=222222222,
        first_name="Камол",
        last_name=None,
        username="kamal",
        language_code="uz",
        restaurant_id=restaurant2.id,
        restaurant=restaurant2,
    )


# ──────────────────────────────────────────
# FASTAPI TEST CLIENT
# ──────────────────────────────────────────
@pytest.fixture
def client(db, agency, restaurant, tg_user):
    """
    TestClient с переопределёнными зависимостями.
    Каждый тест получает изолированный клиент.
    """
    from api import app

    def override_get_db():
        yield db

    def override_get_telegram_user():
        return tg_user

    def override_get_current_agency():
        return agency

    def override_get_current_restaurant_admin():
        return restaurant

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_telegram_user] = override_get_telegram_user
    app.dependency_overrides[get_current_agency] = override_get_current_agency
    app.dependency_overrides[get_current_restaurant_admin] = override_get_current_restaurant_admin

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def agency_token(agency) -> str:
    return create_agency_token(agency)


@pytest.fixture
def restaurant_token(restaurant) -> str:
    return create_restaurant_token(restaurant)


@pytest.fixture
def auth_headers_agency(agency_token) -> dict:
    return {"Authorization": f"Bearer {agency_token}"}


@pytest.fixture
def auth_headers_restaurant(restaurant_token) -> dict:
    return {"Authorization": f"Bearer {restaurant_token}"}


# ──────────────────────────────────────────
# POSTGRES MARKER — автоматический skip на SQLite
# ──────────────────────────────────────────
def _is_sqlite() -> bool:
    return not _is_postgres


def pytest_collection_modifyitems(items):
    """
    Автоматически пропускает тесты с маркером @pytest.mark.postgres
    при запуске на SQLite.

    Для запуска postgres-тестов:
        DATABASE_URL=postgresql://... pytest -m postgres
    """
    if not _is_sqlite():
        return

    skip_marker = pytest.mark.skip(
        reason=(
            "Requires PostgreSQL (uses AT TIME ZONE, EXTRACT::INT). "
            "Run with a real PostgreSQL: DATABASE_URL=postgresql://... pytest -m postgres"
        )
    )
    for item in items:
        if item.get_closest_marker("postgres"):
            item.add_marker(skip_marker)
