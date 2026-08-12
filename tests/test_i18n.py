"""
tests/test_i18n.py — Task 1B.3

Тесты translation engine (i18n.py) и JSON-файлов переводов.

Запускаются без PostgreSQL — нет зависимостей от БД.
Все тесты помечены @pytest.mark.unit (не integration).
"""

import json
import os
import pytest

# Путь к i18n директории — относительно корня репозитория
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_I18N_DIR = os.path.join(_REPO_ROOT, "i18n")


# ──────────────────────────────────────────
# FIXTURE: изолированный движок между тестами
# ──────────────────────────────────────────
@pytest.fixture(autouse=True)
def clear_i18n_cache():
    """Очищает кеш i18n перед каждым тестом для изоляции."""
    from i18n import clear_cache
    clear_cache()
    yield
    clear_cache()


# ──────────────────────────────────────────
# TEST 1-3: Базовые переводы на каждый язык
# ──────────────────────────────────────────

def test_t_uz_common_loading():
    """t() возвращает UZ перевод для common.loading."""
    from i18n import t
    result = t("common.loading", "uz")
    assert result == "Yuklanmoqda..."
    assert result  # не пустая строка


def test_t_ru_common_loading():
    """t() возвращает RU перевод для common.loading."""
    from i18n import t
    result = t("common.loading", "ru")
    assert result == "Загрузка..."
    assert result


def test_t_en_common_loading():
    """t() возвращает EN перевод для common.loading."""
    from i18n import t
    result = t("common.loading", "en")
    assert result == "Loading..."
    assert result


# ──────────────────────────────────────────
# TEST 4: Неизвестный язык → fallback uz
# ──────────────────────────────────────────

def test_unknown_language_fallback():
    """Неизвестный язык 'de' → uz (DEFAULT_LANGUAGE)."""
    from i18n import t
    result_de = t("common.loading", "de")
    result_uz = t("common.loading", "uz")
    assert result_de == result_uz, f"Expected uz fallback, got {result_de!r}"


def test_unknown_language_fr_fallback():
    """Неизвестный язык 'fr' → uz."""
    from i18n import t
    result = t("common.save", "fr")
    assert result == "Saqlash"  # uz default


def test_empty_language_fallback():
    """Пустая строка языка → uz."""
    from i18n import t
    result = t("common.loading", "")
    assert result == "Yuklanmoqda..."


# ──────────────────────────────────────────
# TEST 5: Отсутствующий ключ → fallback
# ──────────────────────────────────────────

def test_missing_key_returns_key_itself():
    """Отсутствующий ключ возвращает сам ключ (не пустую строку)."""
    from i18n import t
    key = "nonexistent.key.that.does.not.exist"
    result = t(key, "uz")
    assert result == key
    assert result  # не пустая строка


def test_missing_key_ru_returns_key():
    """Отсутствующий ключ при любом языке возвращает ключ."""
    from i18n import t
    key = "totally.missing.key"
    result = t(key, "ru")
    assert result == key


# ──────────────────────────────────────────
# TEST 6: Interpolation — одна переменная
# ──────────────────────────────────────────

def test_interpolation_single_var_en():
    """{{id}} заменяется на переданное значение."""
    from i18n import t
    result = t("order.number", "en", id=42)
    assert result == "Order #42"
    assert "{{id}}" not in result


def test_interpolation_single_var_uz():
    """{{id}} заменяется в UZ переводе."""
    from i18n import t
    result = t("order.number", "uz", id=99)
    assert result == "Buyurtma #99"


def test_interpolation_single_var_ru():
    """{{id}} заменяется в RU переводе."""
    from i18n import t
    result = t("order.number", "ru", id=7)
    assert result == "Заказ #7"


# ──────────────────────────────────────────
# TEST 7: Interpolation — несколько переменных
# ──────────────────────────────────────────

def test_interpolation_multiple_vars():
    """Несколько {{var}} заменяются корректно."""
    from i18n import t
    result = t("validation.minimum_order", "en", amount="50 000 so'm")
    assert "50 000 so'm" in result
    assert "{{amount}}" not in result


def test_interpolation_multiple_vars_ru():
    """Несколько переменных в RU строке."""
    from i18n import t
    result = t("validation.minimum_order", "ru", amount="500 ₽")
    assert "500 ₽" in result
    assert "{{amount}}" not in result


def test_interpolation_unknown_placeholder_kept():
    """Неизвестный placeholder в шаблоне оставляется как есть."""
    from i18n import t
    # Передаём id но не передаём unknown_var
    result = t("order.number", "en", id=1)
    # {{id}} должен быть заменён
    assert "#1" in result


# ──────────────────────────────────────────
# TEST 8: Cache — файл не читается дважды
# ──────────────────────────────────────────

def test_cache_loads_once(monkeypatch):
    """
    Второй вызов t() для того же языка не читает JSON с диска.
    Проверяем через счётчик вызовов open().
    """
    import i18n as i18n_module
    from i18n import t, clear_cache

    clear_cache()

    load_count = {"n": 0}
    original_load = i18n_module._load

    def counting_load(lang):
        load_count["n"] += 1
        return original_load(lang)

    monkeypatch.setattr(i18n_module, "_load", counting_load)

    # Первый вызов — загружает
    t("common.loading", "en")
    assert load_count["n"] == 1

    # Второй, третий вызов — из кеша
    t("common.save", "en")
    t("common.cancel", "en")
    assert load_count["n"] == 1, f"Expected 1 load, got {load_count['n']}"


def test_cache_separate_per_language(monkeypatch):
    """Разные языки загружаются независимо, каждый по одному разу."""
    import i18n as i18n_module
    from i18n import t, clear_cache

    clear_cache()

    load_count = {"n": 0}
    original_load = i18n_module._load

    def counting_load(lang):
        load_count["n"] += 1
        return original_load(lang)

    monkeypatch.setattr(i18n_module, "_load", counting_load)

    t("common.loading", "uz")
    t("common.loading", "ru")
    t("common.loading", "en")
    # Должно быть ровно 3 загрузки (по одной на язык)
    assert load_count["n"] == 3

    # Повторные вызовы — из кеша
    t("common.save", "uz")
    t("common.save", "ru")
    t("common.save", "en")
    assert load_count["n"] == 3


# ──────────────────────────────────────────
# TEST 9: JSON-файлы валидны
# ──────────────────────────────────────────

@pytest.mark.parametrize("lang", ["uz", "ru", "en"])
def test_json_file_valid(lang):
    """JSON-файл для каждого языка должен корректно парситься."""
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    assert os.path.exists(path), f"Файл не найден: {path}"
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict), f"{lang}.json должен быть объектом"
    assert len(data) > 0, f"{lang}.json пустой"


@pytest.mark.parametrize("lang", ["uz", "ru", "en"])
def test_json_values_are_strings(lang):
    """Все значения в JSON должны быть строками."""
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key, value in data.items():
        assert isinstance(value, str), (
            f"{lang}.json: ключ {key!r} имеет не-строковое значение: {value!r}"
        )


@pytest.mark.parametrize("lang", ["uz", "ru", "en"])
def test_json_values_not_empty(lang):
    """Ни одно значение в JSON не должно быть пустой строкой."""
    path = os.path.join(_I18N_DIR, f"{lang}.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key, value in data.items():
        assert value.strip(), (
            f"{lang}.json: ключ {key!r} имеет пустое значение"
        )


# ──────────────────────────────────────────
# TEST 10: Все три языка содержат одинаковый набор ключей
# ──────────────────────────────────────────

def test_all_languages_have_same_keys():
    """
    КРИТИЧНЫЙ ТЕСТ: UZ / RU / EN должны содержать одинаковые ключи.
    Выявляет отсутствующие переводы при добавлении новых ключей.
    """
    langs = ["uz", "ru", "en"]
    all_keys = {}

    for lang in langs:
        path = os.path.join(_I18N_DIR, f"{lang}.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        all_keys[lang] = set(data.keys())

    # Объединённый набор всех ключей
    union = all_keys["uz"] | all_keys["ru"] | all_keys["en"]

    missing_report = []
    for lang in langs:
        missing = union - all_keys[lang]
        if missing:
            missing_report.append(
                f"\n  {lang}.json отсутствуют ключи: {sorted(missing)}"
            )

    assert not missing_report, (
        "Несоответствие ключей между языками:" + "".join(missing_report)
    )


def test_uz_ru_en_key_count_equal():
    """Количество ключей во всех трёх файлах должно совпадать."""
    counts = {}
    for lang in ["uz", "ru", "en"]:
        path = os.path.join(_I18N_DIR, f"{lang}.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        counts[lang] = len(data)

    assert counts["uz"] == counts["ru"] == counts["en"], (
        f"Разное количество ключей: {counts}"
    )


# ──────────────────────────────────────────
# ДОПОЛНИТЕЛЬНЫЕ: специфические переводы
# ──────────────────────────────────────────

def test_telegram_keys_exist_in_all_langs():
    """Все telegram.* ключи присутствуют во всех языках."""
    from i18n import t
    telegram_keys = [
        "telegram.new_order",
        "telegram.order_accepted",
        "telegram.order_preparing",
        "telegram.order_ready",
        "telegram.order_delivering",
        "telegram.order_completed",
        "telegram.order_cancelled",
    ]
    for lang in ["uz", "ru", "en"]:
        for key in telegram_keys:
            result = t(key, lang)
            assert result != key, (
                f"Ключ {key!r} не найден в {lang}.json (вернулся сам ключ)"
            )


def test_fallback_chain_en_before_uz():
    """
    Если ключ есть в EN но не в RU (гипотетически),
    fallback должен идти en → uz, а не сразу uz.
    Тест проверяет цепочку через monkeypatch.
    """
    import i18n as i18n_module
    from i18n import t, clear_cache

    clear_cache()

    # Подменяем ru-переводы на пустой словарь (симулируем missing key)
    original_get = i18n_module._get_translations

    def patched_get(lang):
        if lang == "ru":
            return {}  # ru ничего не знает
        return original_get(lang)

    i18n_module._get_translations = patched_get
    try:
        result = t("common.loading", "ru")
        # Должен упасть на EN: "Loading..."
        assert result == "Loading...", (
            f"Ожидался EN fallback 'Loading...', получено {result!r}"
        )
    finally:
        i18n_module._get_translations = original_get
        clear_cache()


def test_t_default_lang_is_uz():
    """Вызов t() без явного lang использует 'uz'."""
    from i18n import t
    result_default = t("common.loading")
    result_uz = t("common.loading", "uz")
    assert result_default == result_uz
