"""
Phase 15 — MC-07: unit-тесты без БД (Spec v3.2: RS-13 f/l/o, §14.2).

Не требуют PostgreSQL и вставки строк: выполняются и на SQLite-job.
Тесты с БД и параллельными запросами — в test_guest_limits.py (marker postgres).
"""

import inspect

import pytest

from auth import TelegramUser
from config import settings
from database import Base
from models.operations import phone_digits
from modules.reservations import service as res_service


def test_limit_defaults_are_5_and_3():
    assert settings.RESERVATION_GUEST_MAX_CREATED_PER_60_MIN == 5
    assert settings.RESERVATION_GUEST_MAX_ACTIVE == 3


def test_rs13_l_limit_numbers_are_not_hardcoded_in_service():
    src = inspect.getsource(res_service)
    assert "settings.RESERVATION_GUEST_MAX_CREATED_PER_60_MIN" in src
    assert "settings.RESERVATION_GUEST_MAX_ACTIVE" in src
    for literal in (">= 5", ">= 3", "== 5", "== 3"):
        assert literal not in src


def test_rs13_o_no_redis_and_no_counter_table():
    src = inspect.getsource(res_service)
    assert "import redis" not in src and "from redis" not in src
    assert not [t for t in Base.metadata.tables if "counter" in t.lower()]


@pytest.mark.parametrize(
    "raw",
    ["+998901234567", "998901234567", "+998 (90) 123-45-67", "+998-90-123-45-67", " +998 90 123 45 67 "],
)
def test_phone_formats_normalize_to_same_digits(raw):
    assert phone_digits(raw) == "998901234567"


def test_phone_digits_edge_values():
    assert phone_digits(None) == ""
    assert phone_digits("") == ""
    assert phone_digits("--- ()") == ""        # других преобразований нет (код страны не добавляется)
    assert phone_digits("٩٩٨") == ""           # только ASCII-цифры 0-9


def test_is_guest_property():
    guest = TelegramUser(id=0, first_name="G", last_name=None, username=None, language_code=None)
    real = TelegramUser(id=111, first_name="U", last_name=None, username=None, language_code=None)
    assert guest.is_guest is True
    assert real.is_guest is False


def test_lock_key_is_stable_and_pair_specific():
    k1 = res_service.guest_limit_lock_key(1, "998901234567")
    assert k1 == res_service.guest_limit_lock_key(1, "998901234567")
    assert k1 != res_service.guest_limit_lock_key(2, "998901234567")
    assert k1 != res_service.guest_limit_lock_key(1, "998901234568")
    assert -(2 ** 63) <= k1 < 2 ** 63
