"""
Phase 15 — DB-09: client_phone_digits, миграции 0030 (M1) и 0031 (M2).

Spec v3.2 §17 DB-09: backfill существующих строк (цифры из client_phone),
добор в M2, индекс (location_id, client_phone_digits, created_at).

Цепочка ревизий проверяется без БД. Поведение SQL и схема — только на PostgreSQL
(marker postgres): backfill использует regexp_replace, а CI `alembic upgrade head`
уже применил 0030/0031 к тестовой БД.
"""

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from alembic.config import Config
from alembic.script import ScriptDirectory
from models import Reservation
from models.operations import phone_digits

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "alembic" / "versions"

# Старый формат данных: разные написания телефона, как у реальных броней до Phase 15.
LEGACY_PHONES = [
    "+998901234567",
    "998901234567",
    "+998 (90) 123-45-67",
    "+998-90-123-45-67",
    "(((((((",              # допускается существующей валидацией, цифр нет
    "٩٩٨٩٠١٢٣٤٥٦٧",         # не-ASCII цифры: SQL и Python должны одинаково их отбросить
]


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M1 = "0030_phase15_reservation_phone_digits.py"
M2 = "0031_phase15_reservation_phone_digits_topup.py"


def test_migration_chain_0029_0030_0031():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    assert script.get_revision("0030").down_revision == "0029"
    assert script.get_revision("0031").down_revision == "0030"


def test_migrations_use_alembic_and_share_backfill_rule():
    m1, m2 = _load(M1), _load(M2)
    assert m1.revision == "0030" and m2.revision == "0031"
    assert "[^0-9]" in m1.BACKFILL_SQL and "[^0-9]" in m2.TOPUP_SQL
    assert "client_phone_digits IS NULL" in m1.BACKFILL_SQL
    assert "client_phone_digits IS NULL" in m2.TOPUP_SQL


def _insert_legacy_rows(db, location):
    """Брони «старого кода»: client_phone_digits = NULL."""
    rows = []
    for phone in LEGACY_PHONES:
        row = Reservation(
            restaurant_id=location.restaurant_id,
            location_id=location.id,
            client_name="legacy",
            client_phone=phone,
            guests_count=2,
            reservation_time=datetime.now(UTC) + timedelta(days=2),
            status="new",
        )
        db.add(row)
        rows.append(row)
    db.flush()
    ids = [r.id for r in rows]
    db.execute(
        text("UPDATE reservations SET client_phone_digits = NULL WHERE id = ANY(:ids)"),
        {"ids": ids},
    )
    db.expire_all()
    return ids


@pytest.mark.postgres
def test_m1_backfill_matches_python_normalization(db, location):
    ids = _insert_legacy_rows(db, location)
    assert all(
        v is None
        for (v,) in db.execute(
            text("SELECT client_phone_digits FROM reservations WHERE id = ANY(:ids)"), {"ids": ids}
        )
    )

    db.execute(text(_load(M1).BACKFILL_SQL))

    got = dict(
        db.execute(
            text("SELECT client_phone, client_phone_digits FROM reservations WHERE id = ANY(:ids)"),
            {"ids": ids},
        ).fetchall()
    )
    for phone in LEGACY_PHONES:
        assert got[phone] == phone_digits(phone), phone
    assert got["+998 (90) 123-45-67"] == "998901234567"
    assert got["((((((("] == ""


@pytest.mark.postgres
def test_m2_topup_fills_only_null_rows(db, location):
    ids = _insert_legacy_rows(db, location)
    keep = _insert_legacy_rows(db, location)[0]
    db.execute(
        text("UPDATE reservations SET client_phone_digits = 'kept-as-is' WHERE id = :id"), {"id": keep}
    )

    db.execute(text(_load(M2).TOPUP_SQL))

    kept = db.execute(
        text("SELECT client_phone_digits FROM reservations WHERE id = :id"), {"id": keep}
    ).scalar()
    assert kept == "kept-as-is"                       # уже заполненное не перезаписывается
    filled = db.execute(
        text("SELECT COUNT(*) FROM reservations WHERE id = ANY(:ids) AND client_phone_digits IS NULL"),
        {"ids": ids},
    ).scalar()
    assert filled == 0                                 # NULL-строки добраны


@pytest.mark.postgres
def test_column_and_index_exist_with_expected_definition(db):
    col = db.execute(
        text(
            "SELECT data_type, character_maximum_length, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'reservations' AND column_name = 'client_phone_digits'"
        )
    ).fetchone()
    assert col is not None
    assert tuple(col) == ("character varying", 50, "YES")

    indexdef = db.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'reservations' AND indexname = 'ix_reservations_location_phone_created'"
        )
    ).scalar()
    assert indexdef is not None
    assert "(location_id, client_phone_digits, created_at)" in indexdef


@pytest.mark.postgres
def test_new_rows_get_digits_from_the_model(db, location):
    row = Reservation(
        restaurant_id=location.restaurant_id,
        location_id=location.id,
        client_name="new",
        client_phone="+998 (93) 555-66-77",
        guests_count=2,
        reservation_time=datetime.now(UTC) + timedelta(days=2),
        status="new",
    )
    db.add(row)
    db.flush()
    db.expire_all()

    stored = db.execute(
        text("SELECT client_phone_digits FROM reservations WHERE id = :id"), {"id": row.id}
    ).scalar()
    assert stored == "998935556677"
