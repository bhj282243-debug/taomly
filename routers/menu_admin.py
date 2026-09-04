"""
routers/menu_admin.py — Taomly Platform

Admin endpoints для управления меню.

Endpoints:
  POST   /upload-photo                              — загрузка фото в R2
  POST   /category/                                 — создать категорию
  PATCH  /category/{category_id}                    — обновить категорию
  DELETE /category/{category_id}                    — удалить категорию
  POST   /product/                                  — создать продукт
  PATCH  /product/{product_id}                      — обновить продукт
  DELETE /product/{product_id}                      — удалить продукт
  POST   /product/{product_id}/variants/            — создать вариант
  GET    /product/{product_id}/variants             — список вариантов
  PATCH  /variant/{variant_id}                      — обновить вариант
  DELETE /variant/{variant_id}                      — удалить вариант
  POST   /product/{product_id}/modifier-groups/     — создать группу модификаторов
  GET    /product/{product_id}/modifier-groups      — список групп
  PATCH  /modifier-group/{group_id}                 — обновить группу
  DELETE /modifier-group/{group_id}                 — удалить группу
  POST   /modifier-group/{group_id}/options/        — создать опцию
  GET    /modifier-group/{group_id}/options         — список опций
  PATCH  /modifier-option/{option_id}               — обновить опцию
  DELETE /modifier-option/{option_id}               — удалить опцию
  PUT    /category/{category_id}/translations/{lang}
  GET    /category/{category_id}/translations
  PUT    /product/{product_id}/translations/{lang}
  GET    /product/{product_id}/translations
  PUT    /variant/{variant_id}/translations/{lang}
  GET    /variant/{variant_id}/translations
  PUT    /modifier-group/{group_id}/translations/{lang}
  GET    /modifier-group/{group_id}/translations
  PUT    /modifier-option/{option_id}/translations/{lang}
  GET    /modifier-option/{option_id}/translations

Все endpoints требуют JWT restaurant admin.

Извлечено из routers/menu.py (R-1 модуляризация).
"""

import io
import logging
import uuid
from typing import List, Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from auth import get_current_restaurant_admin
from config import settings
from limiter import limiter
from database import get_db
from models import (
    Category, CategoryTranslation,
    Location,
    ModifierGroup, ModifierGroupTranslation,
    ModifierOption, ModifierOptionTranslation,
    Product, ProductTranslation,
    ProductVariant, VariantTranslation,
    Restaurant, Subscription, SubscriptionPlan, UsageEvent,
)
from sqlalchemy import func
from schemas import (
    CategoryResponse,
    CategoryTranslationResponse,
    CategoryTranslationUpsert,
    NameTranslationResponse,
    NameTranslationUpsert,
    ProductCreate,
    ProductResponse,
    ProductTranslationResponse,
    ProductTranslationUpsert,
    ProductUpdate,
    CategoryCreate,
    CategoryUpdate,
    VariantCreate,
    VariantUpdate,
    VariantResponse,
    ModifierGroupCreate,
    ModifierGroupUpdate,
    ModifierGroupResponse,
    ModifierOptionCreate,
    ModifierOptionUpdate,
    ModifierOptionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────
# ХЕЛПЕР — id первичной Location ресторана
# ──────────────────────────────────────────
def _get_primary_location_id(db: Session, restaurant_id: int) -> int | None:
    """
    S1-4: Возвращает id первичной Location ресторана для UsageEvent.
    В Stage 1 у каждого ресторана ровно одна Location (migration 0010).
    Используется там где location не в scope (menu admin endpoints).
    Ошибка не пробрасывается — UsageEvent.location_id nullable.
    """
    try:
        loc = (
            db.query(Location.id)
            .filter(Location.restaurant_id == restaurant_id)
            .order_by(Location.id)
            .first()
        )
        return loc[0] if loc else None
    except Exception:
        logger.debug(
            "_get_primary_location_id: не удалось получить location для restaurant_id=%s",
            restaurant_id,
        )
        return None


# ──────────────────────────────────────────
# ХЕЛПЕР — проверка лимита продуктов по тарифу
# ──────────────────────────────────────────
def _check_products_quota(db: Session, restaurant_id: int) -> None:
    """
    Проверяет что ресторан не превысил лимит продуктов по текущему тарифному плану.

    Алгоритм:
      1. Загружает активную подписку ресторана.
      2. Если подписки нет — считает ресторан на Free плане.
      3. Если products_limit == -1 — безлимит, проверка не нужна.
      4. Считает все продукты ресторана (включая недоступные).
      5. Если лимит достигнут — возвращает HTTP 402 с понятным сообщением.

    Вызывается в create_product до любой записи в БД.
    """
    sub = (
        db.query(Subscription)
        .filter(
            Subscription.restaurant_id == restaurant_id,
            Subscription.is_active == True,
        )
        .order_by(Subscription.started_at.desc())
        .first()
    )

    if sub is None:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "Free").first()
        if plan is None:
            return
    else:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        if plan is None:
            return

    if plan.products_limit == -1:
        return

    count = db.query(func.count(Product.id)).filter(
        Product.restaurant_id == restaurant_id,
    ).scalar() or 0

    if count >= plan.products_limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Лимит продуктов тарифа достигнут ({plan.products_limit}). Обновите тарифный план.",
        )


# ──────────────────────────────────────────
# POST /upload-photo — загрузка фото блюда в Cloudflare R2
# ──────────────────────────────────────────

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _get_s3_client():
    """
    Возвращает boto3 S3-клиент для Cloudflare R2.
    Вызывается при каждом запросе — boto3 сам кэширует соединения внутри.
    Не делаем module-level singleton чтобы изменения ENV вступали в силу без рестарта.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _delete_r2_photo(photo_url: str) -> None:
    """
    Удаляет объект из R2 по публичному URL.

    Удаляет только файлы из собственного R2-бакета (URL начинается с R2_PUBLIC_URL).
    Внешние URL (например, старые Telegraph-ссылки) игнорируются без ошибки.
    Ошибки удаления логируются, но не пробрасываются — БД уже обновлена.
    """
    if not photo_url:
        return

    public_base = settings.R2_PUBLIC_URL.rstrip("/")
    if not photo_url.startswith(public_base + "/"):
        logger.info("_delete_r2_photo: URL не из нашего R2, пропускаем: %s", photo_url)
        return

    object_key = photo_url[len(public_base) + 1:]
    if not object_key:
        return

    try:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=object_key)
        logger.info("R2 объект удалён: key=%s", object_key)
    except (BotoCoreError, ClientError):
        logger.exception("Не удалось удалить R2 объект: key=%s", object_key)


@router.post("/upload-photo")
@limiter.limit("20/hour")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
):
    """
    Загружает фото блюда в Cloudflare R2.
    Возвращает публичный URL загруженного файла.

    Ограничения:
      - Только JPEG / PNG / WebP / GIF
      - Максимум 5 MB
      - Требует JWT-авторизации ресторанного администратора
      - Rate limit: 20 запросов в час с одного IP (Foundation Task 11.2)
    """
    # Проверяем что R2 настроен
    if not settings.R2_ACCESS_KEY_ID or not settings.R2_ACCOUNT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище фотографий не настроено. Свяжитесь с поддержкой.",
        )

    # Проверка MIME-типа
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Допустимые форматы: JPEG, PNG, WebP, GIF",
        )

    # Читаем файл и проверяем размер
    data = await file.read()
    if len(data) > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Размер файла превышает 5 MB ({len(data) / 1024 / 1024:.1f} MB)",
        )

    # Проверяем реальный формат файла по magic bytes через Pillow.
    # Content-Type из HTTP-заголовка клиент может подделать — здесь мы убеждаемся,
    # что байты действительно являются изображением поддерживаемого формата.
    # .verify() деструктивен (после него объект Image нельзя использовать),
    # но нам нужна только проверка, не дальнейшая обработка.
    try:
        from PIL import Image, UnidentifiedImageError
        img = Image.open(io.BytesIO(data))
        img.verify()
    except (UnidentifiedImageError, Exception):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл не является изображением или повреждён. Допустимые форматы: JPEG, PNG, WebP, GIF",
        )

    # Генерируем уникальное имя: restaurants/<id>/<uuid>.<ext>
    ext = _MIME_TO_EXT[content_type]
    object_key = f"restaurants/{restaurant.id}/{uuid.uuid4().hex}{ext}"

    # Загружаем в R2
    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception(
            "Ошибка загрузки фото в R2: restaurant_id=%s key=%s",
            restaurant.id, object_key,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ошибка загрузки файла. Попробуйте ещё раз.",
        ) from exc

    public_url = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{object_key}"

    logger.info(
        "Фото загружено: restaurant_id=%s key=%s url=%s",
        restaurant.id, object_key, public_url,
    )

    return {"url": public_url}


# ──────────────────────────────────────────
# POST /category/ — создать категорию (админка)
# ──────────────────────────────────────────
@router.post("/category/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт категорию меню.
    Категория автоматически привязывается к ресторану из JWT-токена.
    Клиент не передаёт restaurant_id — tenant-изоляция гарантирована.
    """
    category = Category(
        restaurant_id=restaurant.id,
        name=data.name,
        sort_order=data.sort_order,
    )
    db.add(category)

    try:
        db.commit()
        db.refresh(category)
    except Exception:
        logger.exception(
            "Ошибка при создании категории: restaurant_id=%s name=%s",
            restaurant.id, data.name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании категории",
        )

    logger.info(
        "Категория создана: category_id=%s name=%s restaurant_id=%s",
        category.id, data.name, restaurant.id,
    )
    return category


# ──────────────────────────────────────────
# PATCH /category/{category_id} — обновить категорию (админка)
# ──────────────────────────────────────────
@router.patch("/category/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    data: CategoryUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет категорию меню.
    Tenant-изоляция: категория ищется только среди категорий ресторана из токена.
    """
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.restaurant_id == restaurant.id,
    ).first()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена",
        )

    if data.name is not None:
        category.name = data.name
    if data.sort_order is not None:
        category.sort_order = data.sort_order

    try:
        db.commit()
        db.refresh(category)
    except Exception:
        logger.exception(
            "Ошибка при обновлении категории: category_id=%s", category_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении категории",
        )

    return category


# ──────────────────────────────────────────
# DELETE /category/{category_id} — удалить категорию (админка)
# ──────────────────────────────────────────
@router.delete("/category/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет категорию и все её продукты (cascade в models.py).
    Tenant-изоляция: категория ищется только среди категорий ресторана из токена.
    """
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.restaurant_id == restaurant.id,
    ).first()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена",
        )

    try:
        db.delete(category)
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при удалении категории: category_id=%s", category_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении категории",
        )

    logger.info(
        "Категория удалена: category_id=%s restaurant_id=%s",
        category_id, restaurant.id,
    )


# ──────────────────────────────────────────
# POST /product/ — создать продукт (админка)
# ──────────────────────────────────────────
@router.post("/product/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт продукт в меню.

    Tenant-изоляция: category_id проверяется на принадлежность ресторану
    из JWT-токена. Нельзя создать продукт в категории чужого ресторана.
    price > 0 проверяется в Pydantic схеме (ProductCreate).
    """
    _check_products_quota(db, restaurant.id)

    category = db.query(Category).filter(
        Category.id == data.category_id,
        Category.restaurant_id == restaurant.id,
    ).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена в этом ресторане",
        )

    product = Product(
        restaurant_id=restaurant.id,
        category_id=data.category_id,
        name=data.name,
        price=data.price,
        description=data.description,
        photo_url=data.photo_url,
        is_available=data.is_available,
        sort_order=data.sort_order,
        is_bestseller=data.is_bestseller,
        is_new=data.is_new,
        is_spicy=data.is_spicy,
        is_chef_choice=data.is_chef_choice,
        is_popular=data.is_popular,
        # Phase 3: расписание доступности
        available_from=data.available_from,
        available_until=data.available_until,
    )
    db.add(product)

    try:
        db.commit()
        db.refresh(product)
    except Exception:
        logger.exception(
            "Ошибка при создании продукта: restaurant_id=%s name=%s",
            restaurant.id, data.name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании продукта",
        )

    logger.info(
        "Продукт создан: product_id=%s name=%s restaurant_id=%s",
        product.id, data.name, restaurant.id,
    )

    # Записываем событие создания продукта для биллинга/аудита.
    # Ошибка записи не откатывает созданный продукт.
    try:
        db.add(UsageEvent(
            restaurant_id=restaurant.id,
            location_id=_get_primary_location_id(db, restaurant.id),  # S1-4
            event_type="product_created",
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Не удалось записать UsageEvent product_created: product_id=%s restaurant_id=%s",
            product.id, restaurant.id,
        )

    return product


# ──────────────────────────────────────────
# PATCH /product/{product_id} — обновить продукт (админка)
# ──────────────────────────────────────────
@router.patch("/product/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    data: ProductUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет продукт.

    Tenant-изоляция: продукт ищется только среди продуктов ресторана из токена.
    Нельзя изменить продукт чужого ресторана зная его ID (IDOR закрыт).
    Если меняется category_id — новая категория тоже проверяется на принадлежность.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    if data.category_id is not None:
        category = db.query(Category).filter(
            Category.id == data.category_id,
            Category.restaurant_id == restaurant.id,
        ).first()
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Категория не найдена в этом ресторане",
            )
        product.category_id = data.category_id

    if data.name is not None:
        product.name = data.name
    if data.price is not None:
        product.price = data.price
    if data.description is not None:
        product.description = data.description
    if data.photo_url is not None:
        old_photo_url = product.photo_url  # запоминаем до изменения
        product.photo_url = data.photo_url
    else:
        old_photo_url = None
    if data.is_available is not None:
        product.is_available = data.is_available
    if data.sort_order is not None:
        product.sort_order = data.sort_order
    if data.is_bestseller is not None:
        product.is_bestseller = data.is_bestseller
    if data.is_new is not None:
        product.is_new = data.is_new
    if data.is_spicy is not None:
        product.is_spicy = data.is_spicy
    if data.is_chef_choice is not None:
        product.is_chef_choice = data.is_chef_choice
    if data.is_popular is not None:
        product.is_popular = data.is_popular
    # Phase 3: расписание доступности. Используем __contains__ None check
    # чтобы отличить "поле не передано" от "передано явно как None (очистить)".
    if "available_from" in data.model_fields_set:
        product.available_from = data.available_from
    if "available_until" in data.model_fields_set:
        product.available_until = data.available_until

    try:
        db.commit()
        db.refresh(product)
    except Exception:
        logger.exception(
            "Ошибка при обновлении продукта: product_id=%s", product_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении продукта",
        )

    logger.info(
        "Продукт обновлён: product_id=%s restaurant_id=%s",
        product_id, restaurant.id,
    )

    # Удаляем старое фото из R2 после успешного commit
    # Удаляем только если фото реально сменилось и старый URL отличается от нового
    if old_photo_url and old_photo_url != product.photo_url:
        _delete_r2_photo(old_photo_url)

    return product


# ──────────────────────────────────────────
# DELETE /product/{product_id} — удалить продукт (админка)
# ──────────────────────────────────────────
@router.delete("/product/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет продукт из меню.
    Tenant-изоляция: продукт ищется только среди продуктов ресторана из токена.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    photo_url = product.photo_url  # запоминаем до удаления

    try:
        db.delete(product)
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при удалении продукта: product_id=%s", product_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении продукта",
        )

    logger.info(
        "Продукт удалён: product_id=%s restaurant_id=%s",
        product_id, restaurant.id,
    )

    # Записываем событие удаления продукта для биллинга/аудита.
    # Ошибка записи не пробрасывается — продукт уже удалён.
    try:
        db.add(UsageEvent(
            restaurant_id=restaurant.id,
            location_id=_get_primary_location_id(db, restaurant.id),  # S1-4
            event_type="product_deleted",
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Не удалось записать UsageEvent product_deleted: product_id=%s restaurant_id=%s",
            product_id, restaurant.id,
        )

    # Удаляем фото из R2 после успешного commit
    if photo_url:
        _delete_r2_photo(photo_url)


# ══════════════════════════════════════════════════════════════════════════════
# S2-3: VARIANT CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/product/{product_id}/variants/",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_variant(
    product_id: int,
    data: VariantCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт вариант продукта.

    Tenant-изоляция P0: product_id проверяется на принадлежность ресторану из JWT.
    Нельзя создать вариант для продукта чужого ресторана.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    variant = ProductVariant(
        product_id=product_id,
        name=data.name,
        price=data.price,
        sort_order=data.sort_order,
        is_active=data.is_active,
        # Phase 3: временная недоступность варианта
        is_available=data.is_available,
    )
    db.add(variant)

    try:
        db.commit()
        db.refresh(variant)
    except Exception:
        logger.exception(
            "Ошибка при создании варианта: product_id=%s name=%s",
            product_id, data.name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании варианта",
        )

    logger.info(
        "Вариант создан: variant_id=%s product_id=%s restaurant_id=%s",
        variant.id, product_id, restaurant.id,
    )
    return variant


@router.get(
    "/product/{product_id}/variants",
    response_model=List[VariantResponse],
)
def list_variants(
    product_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает варианты продукта.

    Tenant-изоляция P0: продукт проверяется на принадлежность ресторану из JWT.
    Сортировка: sort_order ASC, id ASC.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.sort_order.asc(), ProductVariant.id.asc())
        .all()
    )
    return variants


@router.patch("/variant/{variant_id}", response_model=VariantResponse)
def update_variant(
    variant_id: int,
    data: VariantUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет вариант продукта.

    Tenant-изоляция P0: variant загружается через JOIN с Product,
    проверяя Product.restaurant_id == JWT restaurant_id.
    Нельзя изменить вариант продукта чужого ресторана.
    """
    variant = (
        db.query(ProductVariant)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(
            ProductVariant.id == variant_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not variant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Вариант не найден",
        )

    if data.name is not None:
        variant.name = data.name
    if data.price is not None:
        variant.price = data.price
    if data.sort_order is not None:
        variant.sort_order = data.sort_order
    if data.is_active is not None:
        variant.is_active = data.is_active
    # Phase 3: временная недоступность варианта
    if data.is_available is not None:
        variant.is_available = data.is_available

    try:
        db.commit()
        db.refresh(variant)
    except Exception:
        logger.exception(
            "Ошибка при обновлении варианта: variant_id=%s", variant_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении варианта",
        )

    logger.info(
        "Вариант обновлён: variant_id=%s restaurant_id=%s",
        variant_id, restaurant.id,
    )
    return variant


@router.delete("/variant/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    variant_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет вариант продукта.

    Tenant-изоляция P0: variant загружается через JOIN с Product,
    проверяя Product.restaurant_id == JWT restaurant_id.
    """
    variant = (
        db.query(ProductVariant)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(
            ProductVariant.id == variant_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not variant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Вариант не найден",
        )

    try:
        db.delete(variant)
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при удалении варианта: variant_id=%s", variant_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении варианта",
        )

    logger.info(
        "Вариант удалён: variant_id=%s restaurant_id=%s",
        variant_id, restaurant.id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# S2-4: MODIFIER GROUP CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/product/{product_id}/modifier-groups/",
    response_model=ModifierGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_modifier_group(
    product_id: int,
    data: ModifierGroupCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт ModifierGroup для продукта.
    Tenant-изоляция P0: проверка Product.restaurant_id == JWT restaurant_id.
    """
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.restaurant_id == restaurant.id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Продукт не найден")

    group = ModifierGroup(
        product_id=product_id,
        name=data.name,
        min_selections=data.min_selections,
        max_selections=data.max_selections,
        sort_order=data.sort_order,
        is_active=data.is_active,
    )
    db.add(group)
    try:
        db.commit()
        db.refresh(group)
    except Exception:
        logger.exception("Ошибка при создании modifier group: product_id=%s", product_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании группы модификаторов",
        )

    logger.info(
        "ModifierGroup создана: group_id=%s product_id=%s restaurant_id=%s",
        group.id, product_id, restaurant.id,
    )
    return group


@router.get(
    "/product/{product_id}/modifier-groups",
    response_model=List[ModifierGroupResponse],
)
def list_modifier_groups(
    product_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает ModifierGroup продукта.
    Tenant-изоляция P0: проверка Product.restaurant_id == JWT restaurant_id.
    """
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.restaurant_id == restaurant.id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Продукт не найден")

    groups = (
        db.query(ModifierGroup)
        .filter(ModifierGroup.product_id == product_id)
        .order_by(ModifierGroup.sort_order.asc(), ModifierGroup.id.asc())
        .all()
    )
    return groups


@router.patch("/modifier-group/{group_id}", response_model=ModifierGroupResponse)
def update_modifier_group(
    group_id: int,
    data: ModifierGroupUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет ModifierGroup.
    Tenant-изоляция P0: ModifierGroup → Product → restaurant_id.
    После PATCH итоговые min/max обязаны удовлетворять инварианту.
    """
    group = (
        db.query(ModifierGroup)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierGroup.id == group_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа не найдена")

    new_min = data.min_selections if data.min_selections is not None else group.min_selections
    new_max = data.max_selections if data.max_selections is not None else group.max_selections

    if new_min > new_max:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_selections не может быть больше max_selections",
        )

    if data.name is not None:
        group.name = data.name
    if data.min_selections is not None:
        group.min_selections = data.min_selections
    if data.max_selections is not None:
        group.max_selections = data.max_selections
    if data.sort_order is not None:
        group.sort_order = data.sort_order
    if data.is_active is not None:
        group.is_active = data.is_active

    try:
        db.commit()
        db.refresh(group)
    except Exception:
        logger.exception("Ошибка при обновлении modifier group: group_id=%s", group_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении группы модификаторов",
        )

    logger.info(
        "ModifierGroup обновлена: group_id=%s restaurant_id=%s",
        group_id, restaurant.id,
    )
    return group


@router.delete("/modifier-group/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_modifier_group(
    group_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет ModifierGroup (и все его ModifierOption через FK CASCADE).
    Tenant-изоляция P0: ModifierGroup → Product → restaurant_id.
    """
    group = (
        db.query(ModifierGroup)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierGroup.id == group_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа не найдена")

    try:
        db.delete(group)
        db.commit()
    except Exception:
        logger.exception("Ошибка при удалении modifier group: group_id=%s", group_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении группы модификаторов",
        )

    logger.info(
        "ModifierGroup удалена: group_id=%s restaurant_id=%s",
        group_id, restaurant.id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# S2-4: MODIFIER OPTION CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/modifier-group/{group_id}/options/",
    response_model=ModifierOptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_modifier_option(
    group_id: int,
    data: ModifierOptionCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт ModifierOption.
    Tenant-изоляция P0: ModifierGroup → Product → restaurant_id.
    """
    group = (
        db.query(ModifierGroup)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierGroup.id == group_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа не найдена")

    option = ModifierOption(
        modifier_group_id=group_id,
        name=data.name,
        price_adjustment=data.price_adjustment,
        sort_order=data.sort_order,
        is_active=data.is_active,
        # Phase 3: временная недоступность опции
        is_available=data.is_available,
    )
    db.add(option)
    try:
        db.commit()
        db.refresh(option)
    except Exception:
        logger.exception("Ошибка при создании modifier option: group_id=%s", group_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании опции модификатора",
        )

    logger.info(
        "ModifierOption создана: option_id=%s group_id=%s restaurant_id=%s",
        option.id, group_id, restaurant.id,
    )
    return option


@router.get(
    "/modifier-group/{group_id}/options",
    response_model=List[ModifierOptionResponse],
)
def list_modifier_options(
    group_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает ModifierOption группы.
    Tenant-изоляция P0: ModifierGroup → Product → restaurant_id.
    """
    group = (
        db.query(ModifierGroup)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierGroup.id == group_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа не найдена")

    options = (
        db.query(ModifierOption)
        .filter(ModifierOption.modifier_group_id == group_id)
        .order_by(ModifierOption.sort_order.asc(), ModifierOption.id.asc())
        .all()
    )
    return options


@router.patch("/modifier-option/{option_id}", response_model=ModifierOptionResponse)
def update_modifier_option(
    option_id: int,
    data: ModifierOptionUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет ModifierOption.
    Tenant-изоляция P0: ModifierOption → ModifierGroup → Product → restaurant_id.
    """
    option = (
        db.query(ModifierOption)
        .join(ModifierGroup, ModifierOption.modifier_group_id == ModifierGroup.id)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierOption.id == option_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not option:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Опция не найдена")

    if data.name is not None:
        option.name = data.name
    if data.price_adjustment is not None:
        option.price_adjustment = data.price_adjustment
    if data.sort_order is not None:
        option.sort_order = data.sort_order
    if data.is_active is not None:
        option.is_active = data.is_active
    # Phase 3: временная недоступность опции
    if data.is_available is not None:
        option.is_available = data.is_available

    try:
        db.commit()
        db.refresh(option)
    except Exception:
        logger.exception("Ошибка при обновлении modifier option: option_id=%s", option_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении опции модификатора",
        )

    logger.info(
        "ModifierOption обновлена: option_id=%s restaurant_id=%s",
        option_id, restaurant.id,
    )
    return option


@router.delete("/modifier-option/{option_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_modifier_option(
    option_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет ModifierOption.
    Tenant-изоляция P0: ModifierOption → ModifierGroup → Product → restaurant_id.
    """
    option = (
        db.query(ModifierOption)
        .join(ModifierGroup, ModifierOption.modifier_group_id == ModifierGroup.id)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(
            ModifierOption.id == option_id,
            Product.restaurant_id == restaurant.id,
        )
        .first()
    )
    if not option:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Опция не найдена")

    try:
        db.delete(option)
        db.commit()
    except Exception:
        logger.exception("Ошибка при удалении modifier option: option_id=%s", option_id)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении опции модификатора",
        )

    logger.info(
        "ModifierOption удалена: option_id=%s restaurant_id=%s",
        option_id, restaurant.id,
    )


# ══════════════════════════════════════════
# PHASE 4 — TRANSLATION ENDPOINTS
# ══════════════════════════════════════════
#
# 10 endpoints: PUT + GET × 5 entity types
# Все требуют Restaurant Admin JWT.
# PUT = upsert (idempotent, 200 в обоих случаях).
# Невалидный lang → 422 (FastAPI Literal validation).
# Чужая сущность → 404.
# ══════════════════════════════════════════


def _get_category_owned(category_id: int, restaurant: Restaurant, db: Session) -> Category:
    """Загружает Category, проверяет принадлежность ресторану. 404 если чужой."""
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat or cat.restaurant_id != restaurant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
    return cat


def _get_product_owned(product_id: int, restaurant: Restaurant, db: Session) -> Product:
    """Загружает Product, проверяет принадлежность ресторану. 404 если чужой."""
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod or prod.restaurant_id != restaurant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Продукт не найден")
    return prod


def _get_variant_owned(variant_id: int, restaurant: Restaurant, db: Session) -> ProductVariant:
    """Загружает Variant через product → restaurant. 404 если чужой."""
    variant = (
        db.query(ProductVariant)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(ProductVariant.id == variant_id, Product.restaurant_id == restaurant.id)
        .first()
    )
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вариант не найден")
    return variant


def _get_modifier_group_owned(group_id: int, restaurant: Restaurant, db: Session) -> ModifierGroup:
    """Загружает ModifierGroup через product → restaurant. 404 если чужой."""
    group = (
        db.query(ModifierGroup)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(ModifierGroup.id == group_id, Product.restaurant_id == restaurant.id)
        .first()
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа модификаторов не найдена")
    return group


def _get_modifier_option_owned(option_id: int, restaurant: Restaurant, db: Session) -> ModifierOption:
    """Загружает ModifierOption через group → product → restaurant. 404 если чужой."""
    option = (
        db.query(ModifierOption)
        .join(ModifierGroup, ModifierOption.modifier_group_id == ModifierGroup.id)
        .join(Product, ModifierGroup.product_id == Product.id)
        .filter(ModifierOption.id == option_id, Product.restaurant_id == restaurant.id)
        .first()
    )
    if not option:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Опция модификатора не найдена")
    return option


def _upsert_translation(db: Session, model_class, filter_kwargs: dict, update_kwargs: dict):
    """
    Generic upsert для translation записи.
    Ищет по filter_kwargs, обновляет или создаёт.
    Возвращает объект после commit.
    """
    obj = db.query(model_class).filter_by(**filter_kwargs).first()
    if obj:
        for k, v in update_kwargs.items():
            setattr(obj, k, v)
    else:
        obj = model_class(**filter_kwargs, **update_kwargs)
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ──────────────────────────────────────────
# CATEGORY TRANSLATIONS
# ──────────────────────────────────────────

@router.put(
    "/category/{category_id}/translations/{lang}",
    response_model=CategoryTranslationResponse,
)
def upsert_category_translation(
    category_id: int,
    lang: Literal["uz", "ru", "en"],
    data: CategoryTranslationUpsert,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Upsert перевода категории. Idempotent.
    Tenant-изоляция: проверяется принадлежность category ресторану из JWT.
    """
    _get_category_owned(category_id, restaurant, db)
    return _upsert_translation(
        db, CategoryTranslation,
        {"category_id": category_id, "language": lang},
        {"name": data.name},
    )


@router.get(
    "/category/{category_id}/translations",
    response_model=List[CategoryTranslationResponse],
)
def list_category_translations(
    category_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Список существующих переводов категории."""
    _get_category_owned(category_id, restaurant, db)
    return db.query(CategoryTranslation).filter(
        CategoryTranslation.category_id == category_id
    ).all()


# ──────────────────────────────────────────
# PRODUCT TRANSLATIONS
# ──────────────────────────────────────────

@router.put(
    "/product/{product_id}/translations/{lang}",
    response_model=ProductTranslationResponse,
)
def upsert_product_translation(
    product_id: int,
    lang: Literal["uz", "ru", "en"],
    data: ProductTranslationUpsert,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Upsert перевода продукта (name + description). Idempotent."""
    _get_product_owned(product_id, restaurant, db)
    return _upsert_translation(
        db, ProductTranslation,
        {"product_id": product_id, "language": lang},
        {"name": data.name, "description": data.description},
    )


@router.get(
    "/product/{product_id}/translations",
    response_model=List[ProductTranslationResponse],
)
def list_product_translations(
    product_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Список существующих переводов продукта."""
    _get_product_owned(product_id, restaurant, db)
    return db.query(ProductTranslation).filter(
        ProductTranslation.product_id == product_id
    ).all()


# ──────────────────────────────────────────
# VARIANT TRANSLATIONS
# ──────────────────────────────────────────

@router.put(
    "/variant/{variant_id}/translations/{lang}",
    response_model=NameTranslationResponse,
)
def upsert_variant_translation(
    variant_id: int,
    lang: Literal["uz", "ru", "en"],
    data: NameTranslationUpsert,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Upsert перевода варианта продукта. Idempotent."""
    _get_variant_owned(variant_id, restaurant, db)
    return _upsert_translation(
        db, VariantTranslation,
        {"variant_id": variant_id, "language": lang},
        {"name": data.name},
    )


@router.get(
    "/variant/{variant_id}/translations",
    response_model=List[NameTranslationResponse],
)
def list_variant_translations(
    variant_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Список существующих переводов варианта."""
    _get_variant_owned(variant_id, restaurant, db)
    return db.query(VariantTranslation).filter(
        VariantTranslation.variant_id == variant_id
    ).all()


# ──────────────────────────────────────────
# MODIFIER GROUP TRANSLATIONS
# ──────────────────────────────────────────

@router.put(
    "/modifier-group/{group_id}/translations/{lang}",
    response_model=NameTranslationResponse,
)
def upsert_modifier_group_translation(
    group_id: int,
    lang: Literal["uz", "ru", "en"],
    data: NameTranslationUpsert,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Upsert перевода группы модификаторов. Idempotent."""
    _get_modifier_group_owned(group_id, restaurant, db)
    return _upsert_translation(
        db, ModifierGroupTranslation,
        {"modifier_group_id": group_id, "language": lang},
        {"name": data.name},
    )


@router.get(
    "/modifier-group/{group_id}/translations",
    response_model=List[NameTranslationResponse],
)
def list_modifier_group_translations(
    group_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Список существующих переводов группы модификаторов."""
    _get_modifier_group_owned(group_id, restaurant, db)
    return db.query(ModifierGroupTranslation).filter(
        ModifierGroupTranslation.modifier_group_id == group_id
    ).all()


# ──────────────────────────────────────────
# MODIFIER OPTION TRANSLATIONS
# ──────────────────────────────────────────

@router.put(
    "/modifier-option/{option_id}/translations/{lang}",
    response_model=NameTranslationResponse,
)
def upsert_modifier_option_translation(
    option_id: int,
    lang: Literal["uz", "ru", "en"],
    data: NameTranslationUpsert,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Upsert перевода опции модификатора. Idempotent."""
    _get_modifier_option_owned(option_id, restaurant, db)
    return _upsert_translation(
        db, ModifierOptionTranslation,
        {"modifier_option_id": option_id, "language": lang},
        {"name": data.name},
    )


@router.get(
    "/modifier-option/{option_id}/translations",
    response_model=List[NameTranslationResponse],
)
def list_modifier_option_translations(
    option_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """Список существующих переводов опции модификатора."""
    _get_modifier_option_owned(option_id, restaurant, db)
    return db.query(ModifierOptionTranslation).filter(
        ModifierOptionTranslation.modifier_option_id == option_id
    ).all()
