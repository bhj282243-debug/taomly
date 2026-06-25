import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db
from models import Agency, Restaurant

# ──────────────────────────────────────────
# КОНФИГ
# ──────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is required")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

FERNET_KEY = os.getenv("FERNET_KEY", "")

# ──────────────────────────────────────────
# PASSWORD HASHING
# ──────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ──────────────────────────────────────────
# FERNET — шифрование Telegram Bot Token
# ──────────────────────────────────────────
def get_fernet() -> Optional[Fernet]:
    if not FERNET_KEY:
        return None
    return Fernet(FERNET_KEY.encode())


def encrypt_token(token: str) -> str:
    f = get_fernet()
    if not f:
        raise HTTPException(status_code=500, detail="FERNET_KEY не настроен")
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    f = get_fernet()
    if not f:
        raise HTTPException(status_code=500, detail="FERNET_KEY не настроен")
    return f.decrypt(encrypted.encode()).decode()


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
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный или истёкший токен"
        )


# ──────────────────────────────────────────
# BEARER
# ──────────────────────────────────────────
bearer_scheme = HTTPBearer()


# ──────────────────────────────────────────
# ЗАВИСИМОСТИ
# ──────────────────────────────────────────
def get_current_user_payload(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    return decode_token(credentials.credentials)


def get_current_agency(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> Agency:
    payload = decode_token(credentials.credentials)

    if payload.get("role") != "agency_owner":
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    agency_id = payload.get("agency_id")
    if not agency_id:
        raise HTTPException(status_code=401, detail="Невалидный токен")

    agency = db.query(Agency).filter(
        Agency.id == agency_id,
        Agency.is_active == True
    ).first()

    if not agency:
        raise HTTPException(status_code=404, detail="Агентство не найдено")

    return agency


def get_current_restaurant_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> Restaurant:
    payload = decode_token(credentials.credentials)

    if payload.get("role") != "restaurant_admin":
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    restaurant_id = payload.get("restaurant_id")
    if not restaurant_id:
        raise HTTPException(status_code=401, detail="Невалидный токен")

    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True
    ).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    return restaurant
