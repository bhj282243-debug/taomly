"""
Phase 12: Drop legacy Telegram fields from restaurants table.

Revision ID: 0023
Revises: 0022

Changes:
    Phase 12 moved all Telegram credentials to Location (ADR-001 source of truth).
    Restaurant.telegram_bot_token_encrypted and Restaurant.telegram_dispatcher_id
    were legacy columns kept during the S1-8 transition (Invariant I-2).
    All runtime code now reads/writes Location Telegram fields exclusively.

    A. restaurants.telegram_bot_token_encrypted — TEXT NULL — DROP
    B. restaurants.telegram_dispatcher_id       — BIGINT NULL — DROP

    Location columns are NOT touched — they remain the sole authority.

    Data migration: NOT REQUIRED.
    Location.telegram_bot_token_encrypted already contains correct data
    (maintained by Invariant I-2 throughout the S1-8 transition).

    Downgrade: re-adds the columns as nullable without data restoration.
    Data is not restored because Location is the authoritative source.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ──────────────────────────────────────────
# Revision identifiers
# ──────────────────────────────────────────

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Remove legacy Telegram credential columns from restaurants.

    These columns were the source of truth before S1-8 and were kept
    as a transitional copy (Invariant I-2) while Phase 12 migrated all
    runtime code to Location. They are now safe to remove.
    """
    op.drop_column("restaurants", "telegram_bot_token_encrypted")
    op.drop_column("restaurants", "telegram_dispatcher_id")


def downgrade() -> None:
    """
    Re-add legacy Telegram columns to restaurants as nullable.

    Data is NOT restored — Location remains the source of truth.
    This downgrade is structural only, allowing the schema to match
    the pre-0023 state for rollback purposes.
    """
    op.add_column(
        "restaurants",
        sa.Column(
            "telegram_dispatcher_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.add_column(
        "restaurants",
        sa.Column(
            "telegram_bot_token_encrypted",
            sa.Text(),
            nullable=True,
        ),
    )
