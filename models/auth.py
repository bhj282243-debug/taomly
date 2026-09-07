"""
models/auth.py — Taomly Platform
JWT Revocation List.

Изменения v6 (Security — JWT Revocation):
  - RevokedToken: таблица отозванных JWT (jti revocation list).
    jti        — UUID4, уникальный идентификатор токена из JWT payload.
    token_type — "access" | "refresh". Зарезервировано для Refresh Token (пункт C-4).
                 Позволяет отзывать только refresh без инвалидации access, и наоборот.
    expires_at — копия exp из JWT. Записи старше expires_at — кандидаты на очистку.
    revoked_at — UTC-время отзыва (аудит, forensics).

    Индексы:
      uq_revoked_tokens_jti       — O(1) lookup при каждом запросе.
      ix_revoked_tokens_expires_at — ускоряет purge-запрос (DELETE WHERE expires_at < NOW()).

    Нет FK на Agency/Restaurant — намеренно. Удаление агентства не должно каскадно
    удалять revocation-записи: токены должны оставаться отозванными до истечения.
"""

from sqlalchemy import BigInteger, Column, Index, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func

from database import Base


# ──────────────────────────────────────────
# REVOKED TOKENS (JWT Revocation List)
# ──────────────────────────────────────────
class RevokedToken(Base):
    """
    JWT Revocation List — хранит отозванные токены до их естественного истечения.

    Архитектура:
      - При выдаче каждый JWT получает уникальный jti (UUID4).
      - При logout → INSERT jti сюда (атомарно через ON CONFLICT DO NOTHING).
      - При каждом запросе → SELECT по jti (индекс, O(1) ~1ms на Neon).
      - После exp токен устарел сам по себе → запись можно удалить.

    token_type зарезервирован для Refresh Token (пункт C-4):
      - "access"  — обычный access token (8 ч)
      - "refresh" — refresh token (7 дней, пункт C-4)
      Позволит отзывать refresh без инвалидации access, и наоборот.

    Очистка через purge_expired_revoked_tokens() в auth.py.
    Вызвать вручную или из maintenance-задачи:
        DELETE FROM revoked_tokens WHERE expires_at < NOW();
    """
    __tablename__ = "revoked_tokens"

    id         = Column(BigInteger, primary_key=True)
    jti        = Column(String(36), nullable=False)
    token_type = Column(String(16), nullable=False, server_default="access")  # access | refresh
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
        Index("ix_revoked_tokens_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<RevokedToken jti={self.jti!r} type={self.token_type!r} expires={self.expires_at}>"
