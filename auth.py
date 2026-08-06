"""
auth.py — Taomly Platform
Аутентификация: JWT, bcrypt, Fernet, Telegram initData HMAC-SHA256.

Изменения v5:
  - Все env-переменные читаются из config.py (единый источник)
  - Убраны прямые os.getenv вызовы

Изменения v6 (Security — JWT Revocation):
  - Все JWT содержат поле jti (UUID4) — уникальный идентификатор токена.
  - decode_token(token, db) — проверяет revocation при каждом запросе.
    db обязателен в защищённом контексте (передаётся из get_current_*).
  - get_current_* сохраняют payload в request.state.jwt_payload —
    logout читает его оттуда, избегая повторного decode и SELECT.
  - revoke_token(payload, db) — атомарный INSERT ON CONFLICT DO NOTHING.
    Нет race condition: уникальный индекс + один запрос к БД.
  - purge_expired_revoked_tokens(db) — удаляет устаревшие записи.
    Вызывается из maintenance-задачи или вручную. Не cron-зависимость.
  - token_type в payload ("access" | "refresh") — подготовка к C-4 (JWT Refresh).
"""

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from urllib.parse import parse_qsl, unquote

from cryptography.fernet import Fernet, InvalidToken as FernetInvalidToken
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Agency, Restaurant, RevokedToken

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

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
_fernet = Fernet(settings.FERNET_KEY.encode())


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
    """
    id: int
    first_name: str
    last_name: Optional[str]
    username: Optional[str]
    language_code: Optional[str]

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

    White Label Multi-Tenant: принимает bot_token конкретного ресторана.
    Алгоритм по официальной документации Telegram.
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

    # Проверка auth_date (Replay Attack)
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
    if age_seconds > settings.MAX_INIT_DATA_AGE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"initData устарела (возраст {age_seconds}с, "
                f"максимум {settings.MAX_INIT_DATA_AGE_SECONDS}с). "
                "Перезапустите Mini App."
            ),
        )

    # Верификация подписи
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

    # Извлекаем и парсим user
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
    x_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
    x_restaurant_id: int = Header(..., alias="X-Restaurant-Id"),
    db: Session = Depends(get_db),
) -> TelegramUser:
    """
    FastAPI-зависимость для клиентских роутеров Mini App.
    Загружает ресторан, расшифровывает токен бота, верифицирует initData.
    Если initData отсутствует (браузер/PWA) — создаём гостевого пользователя.
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == x_restaurant_id,
        Restaurant.is_active == True,
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )

    # Если initData есть — верифицируем через Telegram HMAC
    if x_init_data:
        if not restaurant.telegram_bot_token_encrypted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Telegram Bot не настроен для этого ресторана",
            )
        bot_token = decrypt_token(restaurant.telegram_bot_token_encrypted)
        user_dict = verify_telegram_init_data(x_init_data, bot_token)
        return TelegramUser.from_dict(user_dict, restaurant)

    # Без initData — гостевой пользователь (браузер / PWA)
    guest_dict = {
        "id": 0,
        "first_name": "Guest",
        "last_name": None,
        "username": None,
        "language_code": "uz",
    }
    return TelegramUser.from_dict(guest_dict, restaurant)


# ──────────────────────────────────────────
# JWT
# ──────────────────────────────────────────
def _build_token(payload_extra: dict, expire_hours: int) -> str:
    """
    Внутренний хелпер: собирает JWT с jti, exp, token_type.
    token_type позволит C-4 (Refresh Token) различать access и refresh
    без изменения архитектуры revocation.
    """
    exp = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    payload = {
        "jti": str(uuid.uuid4()),
        "exp": exp,
        "token_type": "access",   # C-4: refresh tokens будут "refresh"
        **payload_extra,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_agency_token(agency: Agency) -> str:
    return _build_token(
        {
            "sub":       str(agency.id),
            "role":      "agency_owner",
            "agency_id": agency.id,
        },
        expire_hours=settings.ACCESS_TOKEN_EXPIRE_HOURS,
    )


def create_restaurant_token(restaurant: Restaurant) -> str:
    return _build_token(
        {
            "sub":           str(restaurant.id),
            "role":          "restaurant_admin",
            "restaurant_id": restaurant.id,
            "agency_id":     restaurant.agency_id,
        },
        expire_hours=settings.ACCESS_TOKEN_EXPIRE_HOURS,
    )


def decode_token(token: str, db: Session) -> dict:
    """
    Декодирует и валидирует JWT. Проверяет revocation через БД.

    db обязателен — нет silent bypass revocation.
    Вызывается только из get_current_* зависимостей.

    Токены без jti (выданные до v6) получат 401 —
    пользователь войдёт заново и получит токен с jti.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный или истёкший токен",
        )

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен устарел — войдите снова",
        )

    # Один SELECT по уникальному индексу ~1ms на Neon
    revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен отозван. Выполните вход снова.",
        )

    return payload


def revoke_token(
    payload: dict,
    db: Session,
    token_type: Literal["access", "refresh"] = "access",
) -> None:
    """
    Атомарно добавляет jti в revocation list.

    Использует INSERT ... ON CONFLICT DO NOTHING — нет race condition,
    нет предварительного SELECT. Один запрос к БД.

    token_type зарезервирован для C-4 (Refresh Token):
    при logout потребуется отозвать оба токена разными вызовами.

    При любой ошибке БД — rollback и логирование. Не бросает исключение:
    если revocation не записалась — безопаснее вернуть 200 и залогировать,
    чем ломать logout пользователя (токен истечёт по exp в любом случае).
    """
    jti = payload.get("jti")
    if not jti:
        logger.warning("revoke_token: jti отсутствует в payload — пропуск")
        return

    exp_ts = payload.get("exp")
    if exp_ts:
        expires_at = datetime.fromtimestamp(float(exp_ts), tz=timezone.utc)
    else:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS)

    try:
        stmt = (
            pg_insert(RevokedToken)
            .values(jti=jti, token_type=token_type, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["jti"])
        )
        db.execute(stmt)
        db.commit()
        logger.info("JWT отозван: jti=%s type=%s", jti, token_type)
    except Exception:
        db.rollback()
        logger.exception("revoke_token: ошибка записи в БД jti=%s", jti)


def purge_expired_revoked_tokens(db: Session) -> int:
    """
    Удаляет записи revoked_tokens с истёкшим expires_at.

    Вызывать из maintenance-задачи или вручную из Neon SQL Editor:
        DELETE FROM revoked_tokens WHERE expires_at < NOW();

    Возвращает количество удалённых записей.

    Безопасно: истёкшие токены недействительны по exp в любом случае —
    их присутствие или отсутствие в таблице не влияет на безопасность.
    """
    try:
        result = db.execute(
            text("DELETE FROM revoked_tokens WHERE expires_at < NOW()")
        )
        db.commit()
        deleted = result.rowcount
        logger.info("purge_expired_revoked_tokens: удалено %d записей", deleted)
        return deleted
    except Exception:
        db.rollback()
        logger.exception("purge_expired_revoked_tokens: ошибка")
        return 0


# ──────────────────────────────────────────
# BEARER
# ──────────────────────────────────────────
bearer_scheme = HTTPBearer()


# ──────────────────────────────────────────
# DEPENDS — Agency Owner
# ──────────────────────────────────────────
def get_current_agency(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Agency:
    """
    Зависимость для роутеров Agency Owner.

    Сохраняет декодированный payload в request.state.jwt_payload —
    logout читает его оттуда без повторного decode и SELECT к revoked_tokens.
    """
    payload = decode_token(credentials.credentials, db=db)
    request.state.jwt_payload = payload  # для logout

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
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Restaurant:
    """
    Зависимость для роутеров ресторанного администратора.

    Сохраняет декодированный payload в request.state.jwt_payload —
    logout читает его оттуда без повторного decode и SELECT к revoked_tokens.
    """
    payload = decode_token(credentials.credentials, db=db)
    request.state.jwt_payload = payload  # для logout

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
