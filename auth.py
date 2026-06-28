"""
auth.py — Taomly Platform
Аутентификация: JWT, bcrypt, Fernet, Telegram initData HMAC-SHA256.

Изменения v4:
  - TelegramUser получил поле restaurant_id и ссылку на объект restaurant —
    устранён AttributeError в orders.py (tg_user.restaurant_id)
  - get_telegram_user() возвращает TelegramUser с вложенным restaurant,
    чтобы роутеры не делали повторный SQL-запрос (убран лишний db.query)
  - TelegramContext переименован в TelegramUser для простоты —
    один объект содержит и пользователя и его ресторан
"""

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, unquote

from cryptography.fernet import Fernet, InvalidToken as FernetInvalidToken
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import Agency, Restaurant

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# КОНФИГ
# ──────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is required. "
        "Сгенерируй: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_FERNET_KEY_RAW = os.getenv("FERNET_KEY")
if not _FERNET_KEY_RAW:
    raise RuntimeError(
        "FERNET_KEY is required для шифрования Telegram Bot Token. "
        "Сгенерируй: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "24"))

# initData старше этого порога отклоняется (Replay Attack).
# Telegram рекомендует 86400 (24ч). Для повышенной безопасности — 3600 (1ч).
MAX_INIT_DATA_AGE_SECONDS = int(os.getenv("MAX_INIT_DATA_AGE_SECONDS", "86400"))

# ──────────────────────────────────────────
# PASSWORD HASHING (bcrypt)
# ──────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ──────────────────────────────────────────
# FERNET — шифрование/расшифровка Bot Token
# ──────────────────────────────────────────
_fernet = Fernet(_FERNET_KEY_RAW.encode())


def encrypt_token(token: str) -> str:
    """Шифрует токен бота перед сохранением в БД."""
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """
    Расшифровывает токен бота из БД.
    Бросает HTTPException 401 если данные повреждены или FERNET_KEY сменился.
    """
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except FernetInvalidToken:
        logger.error("Fernet decrypt failed: токен повреждён или ключ изменён")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не удалось расшифровать токен бота. Обратитесь к администратору.",
        )
    except Exception as exc:
        logger.exception("Неожиданная ошибка при расшифровке токена: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка шифрования.",
        )


# ──────────────────────────────────────────
# TELEGRAM USER — верифицированный контекст запроса
# ──────────────────────────────────────────
@dataclass
class TelegramUser:
    """
    Верифицированный контекст Telegram Mini App запроса.

    Содержит как данные пользователя из initData, так и загруженный
    объект ресторана — чтобы роутеры не делали повторный SQL-запрос.

    Поля пользователя соответствуют объекту User из Telegram WebApp API.
    """
    # Данные пользователя из initData
    id: int
    first_name: str
    last_name: Optional[str]
    username: Optional[str]
    language_code: Optional[str]

    # Контекст ресторана — загружен и проверен в get_telegram_user()
    # Роутеры используют напрямую, без повторного SQL-запроса
    restaurant_id: int = field(default=0)
    restaurant: Optional[Restaurant] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict, restaurant: Restaurant) -> "TelegramUser":
        return cls(
            id=int(data["id"]),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name"),
            username=data.get("username"),
            language_code=data.get("language_code"),
            restaurant_id=restaurant.id,
            restaurant=restaurant,
        )

    @property
    def display_name(self) -> str:
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name


# ──────────────────────────────────────────
# TELEGRAM initData ВЕРИФИКАЦИЯ
# ──────────────────────────────────────────
def verify_telegram_init_data(init_data: str, bot_token: str) -> dict:
    """
    Верифицирует initData от Telegram Mini App.

    White Label Multi-Tenant: принимает bot_token конкретного ресторана,
    а не глобальный токен платформы. Каждый ресторан проверяет своим ботом.

    Алгоритм (официальная документация Telegram):
      1. Разбиваем init_data на пары ключ=значение
      2. Извлекаем и удаляем поле hash
      3. Сортируем оставшиеся пары по ключу, соединяем через \n
      4. secret_key = HMAC-SHA256(key="WebAppData", msg=bot_token)
      5. expected = HMAC-SHA256(key=secret_key, msg=data_check_string)
      6. Сравниваем через compare_digest (защита от timing attack)
      7. Проверяем auth_date — защита от Replay Attack

    Returns:
        dict с распарсенными полями initData (включая user)

    Raises:
        HTTPException 401 при невалидной подписи, истёкших или неполных данных
    """
    if not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="initData отсутствует",
        )

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Поле hash отсутствует в initData",
        )

    # ── Проверка auth_date (Replay Attack) ──────────────────────────
    auth_date_str = parsed.get("auth_date")
    if not auth_date_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Поле auth_date отсутствует в initData",
        )
    try:
        auth_date = int(auth_date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный формат auth_date",
        )

    age_seconds = int(time.time()) - auth_date
    if age_seconds < 0:
        logger.warning("Telegram initData: auth_date из будущего (%s)", auth_date)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный auth_date: время из будущего",
        )
    if age_seconds > MAX_INIT_DATA_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"initData устарела (возраст {age_seconds}с, "
                f"максимум {MAX_INIT_DATA_AGE_SECONDS}с). "
                "Перезапустите Mini App."
            ),
        )

    # ── Верификация подписи ──────────────────────────────────────────
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        logger.warning(
            "Telegram initData: невалидный hash. "
            "Возможная атака или неверный bot_token для ресторана."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидная подпись Telegram initData",
        )

    # ── Извлекаем и парсим user ──────────────────────────────────────
    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Поле user отсутствует в initData",
        )

    try:
        user_dict = json.loads(unquote(user_json))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Не удалось распарсить user из initData: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный формат user в initData",
        )

    if "id" not in user_dict:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Поле id отсутствует в user initData",
        )

    return user_dict


# ──────────────────────────────────────────
# DEPENDS — Telegram Mini App клиент
# ──────────────────────────────────────────
def get_telegram_user(
    x_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
    x_restaurant_id: int = Header(..., alias="X-Restaurant-Id"),
    db: Session = Depends(get_db),
) -> TelegramUser:
    """
    FastAPI-зависимость для клиентских роутеров Mini App.

    Фронтенд передаёт два заголовка:
      X-Telegram-Init-Data: <window.Telegram.WebApp.initData>
      X-Restaurant-Id:      <restaurant.id>

    Что делает:
      1. Загружает ресторан из БД по X-Restaurant-Id
      2. Проверяет наличие бота у ресторана
      3. Расшифровывает токен бота через Fernet
      4. Верифицирует initData HMAC-SHA256 токеном этого бота
      5. Проверяет auth_date (Replay Attack)
      6. Возвращает TelegramUser с вложенным restaurant

    Роутеры получают всё необходимое из одного объекта:
      tg_user.id            — верифицированный Telegram ID
      tg_user.restaurant_id — ID ресторана (из заголовка, проверен)
      tg_user.restaurant    — ORM-объект Restaurant (без повторного SQL)
      tg_user.display_name  — имя для client_name

    Использование:
        @router.post("/orders/")
        def create_order(
            data: OrderCreate,
            tg_user: TelegramUser = Depends(get_telegram_user),
            db: Session = Depends(get_db),
        ):
            restaurant = tg_user.restaurant  # уже загружен, SQL не нужен
            order = Order(
                restaurant_id=tg_user.restaurant_id,
                client_telegram_id=tg_user.id,
                ...
            )
    """
    # Загружаем ресторан — единственный SQL-запрос для всего клиентского контекста
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == x_restaurant_id,
        Restaurant.is_active == True,
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    if not restaurant.telegram_bot_token_encrypted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram Bot не настроен для этого ресторана",
        )

    # Расшифровываем токен бота этого конкретного ресторана
    bot_token = decrypt_token(restaurant.telegram_bot_token_encrypted)

    # Верифицируем initData токеном именно этого ресторана
    user_dict = verify_telegram_init_data(x_init_data, bot_token)

    # Возвращаем единый объект контекста: пользователь + ресторан
    return TelegramUser.from_dict(user_dict, restaurant)


# ──────────────────────────────────────────
# JWT
# ──────────────────────────────────────────
def create_agency_token(agency: Agency) -> str:
    payload = {
        "sub": str(agency.id),
        "role": "agency_owner",
        "agency_id": agency.id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_restaurant_token(restaurant: Restaurant) -> str:
    payload = {
        "sub": str(restaurant.id),
        "role": "restaurant_admin",
        "restaurant_id": restaurant.id,
        "agency_id": restaurant.agency_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный или истёкший токен",
        )


# ──────────────────────────────────────────
# BEARER
# ──────────────────────────────────────────
bearer_scheme = HTTPBearer()


# ──────────────────────────────────────────
# DEPENDS — Agency Owner
# ──────────────────────────────────────────
def get_current_agency(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agency:
    """
    Зависимость для роутеров Agency Owner.
    Проверяет JWT, role == 'agency_owner', наличие агентства в БД.
    """
    payload = decode_token(credentials.credentials)

    if payload.get("role") != "agency_owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён: требуется роль agency_owner",
        )

    agency_id = payload.get("agency_id")
    if not agency_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен: отсутствует agency_id",
        )

    agency = db.query(Agency).filter(
        Agency.id == agency_id,
        Agency.is_active == True,
    ).first()

    if not agency:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Агентство не найдено или деактивировано",
        )

    return agency


# ──────────────────────────────────────────
# DEPENDS — Restaurant Admin
# ──────────────────────────────────────────
def get_current_restaurant_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Restaurant:
    """
    Зависимость для роутеров ресторанного администратора.
    Проверяет JWT, role == 'restaurant_admin', наличие ресторана в БД.
    """
    payload = decode_token(credentials.credentials)

    if payload.get("role") != "restaurant_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён: требуется роль restaurant_admin",
        )

    restaurant_id = payload.get("restaurant_id")
    if not restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен: отсутствует restaurant_id",
        )

    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True,
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден или деактивирован",
        )

    return restaurant
