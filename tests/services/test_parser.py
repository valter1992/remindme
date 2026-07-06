"""Тесты клетки services: ``parse_remind_time``, ``ParsedReminder``, ``ParseError``.

Контракт-тесты проверяют форму фасада и API (имена, сигнатура, DTO-поля,
варианты ``ParseError.kind``); logic-тесты — дословно перенесённые сценарии из
дизайна (Test Stack Trace и дополнительные параметризованные). ``now``
фиксируется явно (aware UTC); ``datetime.now()``/БД/Telegram не используются.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from remindme.services import ParsedReminder, ParseError, parse_remind_time

_NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_parser_entities() -> None:
    """Фасад экспонирует парсер и DTO как вызываемые/модели."""
    assert callable(parse_remind_time)
    assert isinstance(ParsedReminder, type)
    assert isinstance(ParseError, type)


def test_dtos_are_pydantic_models() -> None:
    """DTO унаследованы от ``pydantic.BaseModel``."""
    assert issubclass(ParsedReminder, BaseModel)
    assert issubclass(ParseError, BaseModel)


def test_parsed_reminder_fields() -> None:
    """``ParsedReminder`` содержит поля ``remind_at_utc`` и ``text``."""
    fields = ParsedReminder.model_fields
    assert "remind_at_utc" in fields
    assert "text" in fields


def test_parse_error_kind_field() -> None:
    """``ParseError`` содержит поле ``kind``."""
    assert "kind" in ParseError.model_fields


def test_parse_remind_time_signature() -> None:
    """``parse_remind_time`` принимает ``raw/now/timezone/default_time``."""
    result = parse_remind_time(
        raw="напомни через 15 минут дело",
        now=_NOW,
        timezone="Europe/Moscow",
        default_time="09:00",
    )
    assert isinstance(result, (ParsedReminder, ParseError))


@pytest.mark.parametrize(
    "kind",
    [
        "invalid_format",
        "date_not_exist",
        "past",
        "horizon_exceeded",
        "empty_text",
        "text_too_long",
        "nonexistent_time",
        "ambiguous_time",
    ],
)
def test_parse_error_kinds_reachable(kind: str) -> None:
    """Все 8 вариантов ``ParseError.kind`` допустимы как значение поля."""
    assert ParseError(kind=kind).kind == kind


# --- Позитивные тесты (Test Stack Trace) ---


def test_parse_remind_time_relative_minutes() -> None:
    """Относительное время «через N минут» — сдвиг момента и очистка текста."""
    result = parse_remind_time(
        "напомни мне через 15 минут проверить духовку",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParsedReminder)
    assert result.remind_at_utc == _NOW + timedelta(minutes=15)
    assert result.text == "проверить духовку"


def test_parse_remind_time_absolute_command() -> None:
    """Режим ``/remind``: split по ``|``, локализация Moscow (UTC+3) → 15:00 UTC."""
    now = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    result = parse_remind_time(
        "/remind 2026-06-24 18:00 | Купить продукты",
        now,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParsedReminder)
    assert result.remind_at_utc == datetime(2026, 6, 24, 15, 0, tzinfo=UTC)
    assert result.text == "Купить продукты"


def test_parse_remind_time_horizon_boundary_in_range() -> None:
    """Ровно 365 дней принимается (off-by-one регресс)."""
    result = parse_remind_time(
        "напомни через 365 дней годовой план",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParsedReminder)
    assert result.remind_at_utc == _NOW + timedelta(days=365)


# --- Негативные тесты (Test Stack Trace) ---


def test_parse_remind_time_past() -> None:
    """«сегодня 19:00» при текущем 20:00 локально → ``past``."""
    now = datetime(2026, 6, 24, 17, 0, 0, tzinfo=UTC)  # 20:00 Moscow

    result = parse_remind_time(
        "напомни сегодня в 19:00 проверить почту",
        now,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "past"


def test_parse_remind_time_unsupported_phrase() -> None:
    """Ни один из 5 шаблонов не matched → ``invalid_format``."""
    result = parse_remind_time(
        "напомни в пятницу вечером позвонить врачу",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "invalid_format"


def test_parse_remind_time_horizon_exceeded() -> None:
    """Горизонт > 365 дней → ``horizon_exceeded``."""
    result = parse_remind_time(
        "напомни через 400 дней долгий план",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "horizon_exceeded"


def test_parse_remind_time_text_too_long() -> None:
    """Текст 501 символ → ``text_too_long``."""
    result = parse_remind_time(
        "напомни через 15 минут " + "а" * 501,
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "text_too_long"


def test_parse_remind_time_empty_text() -> None:
    """Пустой текст после очистки временной части → ``empty_text``."""
    result = parse_remind_time(
        "напомни через 15 минут ",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "empty_text"


def test_parse_remind_time_date_not_exist() -> None:
    """31 февраля не существует → ``date_not_exist`` (до локализации/DST)."""
    result = parse_remind_time(
        "напомни 31.02.2026 в 18:00 совещание",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "date_not_exist"


def test_parse_remind_time_dst_nonexistent() -> None:
    """Весенний перевод Berlin — локальное время в провале → ``nonexistent_time``."""
    result = parse_remind_time(
        "напомни 29.03.2026 в 02:30 совещание",
        _NOW,
        "Europe/Berlin",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "nonexistent_time"


def test_parse_remind_time_ambiguous_time() -> None:
    """Осеннее наложение Berlin — двусмысленное локальное время → ``ambiguous_time``."""
    result = parse_remind_time(
        "напомни 25.10.2026 в 02:30 совещание",
        _NOW,
        "Europe/Berlin",
        "09:00",
    )

    assert isinstance(result, ParseError)
    assert result.kind == "ambiguous_time"


# --- Дополнительные параметризованные и граничные тесты ---


@pytest.mark.parametrize(
    ("unit", "seconds_per_unit"),
    [
        ("минуту", 60),
        ("минуты", 60),
        ("минут", 60),
        ("час", 3600),
        ("часа", 3600),
        ("часов", 3600),
        ("день", 86400),
        ("дня", 86400),
        ("дней", 86400),
    ],
)
@pytest.mark.parametrize(
    ("raw_template", "expected_text"),
    [
        ("напомни через 2 {unit} дело", "дело"),
        ("напомни мне через 2 {unit} дело", "дело"),
        ("НАПОМНИ ЧЕРЕЗ 2 {unit_upper} ДЕЛО", "ДЕЛО"),
        ("напомни через 2 {unit} | дело", "дело"),
    ],
)
def test_parse_remind_time_unit_forms(
    unit: str,
    seconds_per_unit: int,
    raw_template: str,
    expected_text: str,
) -> None:
    """Все формы единиц, регистронезависимость, «мне», ``|``-разделитель.

    Регистр текста сохраняется (парсер не нормализует пользовательский текст).
    """
    raw = raw_template.format(unit=unit, unit_upper=unit.upper())

    result = parse_remind_time(raw, _NOW, "Europe/Moscow", "09:00")

    assert isinstance(result, ParsedReminder)
    assert result.remind_at_utc == _NOW + timedelta(seconds=2 * seconds_per_unit)
    assert result.text == expected_text


@pytest.mark.parametrize(
    ("n", "expect_ok"),
    [
        (1, True),
        (527040, False),  # 366 дней в минутах — горизонт превышен
    ],
)
def test_parse_remind_time_n_boundaries(n: int, expect_ok: bool) -> None:
    """Положительность N и горизонт: N=1 → OK, превышающее 365 дней → exceeded."""
    result = parse_remind_time(
        f"напомни через {n} минут дело",
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    if expect_ok:
        assert isinstance(result, ParsedReminder)
        assert result.remind_at_utc == _NOW + timedelta(minutes=n)
    else:
        assert isinstance(result, ParseError)
        assert result.kind == "horizon_exceeded"


@pytest.mark.parametrize(
    ("length", "expect_kind"),
    [
        (1, None),  # ParsedReminder
        (500, None),  # ParsedReminder
        (501, "text_too_long"),
    ],
)
def test_parse_remind_time_text_length_boundaries(
    length: int,
    expect_kind: str | None,
) -> None:
    """Границы длины текста 1/500/501."""
    result = parse_remind_time(
        "напомни через 1 минуту " + "x" * length,
        _NOW,
        "Europe/Moscow",
        "09:00",
    )

    if expect_kind is None:
        assert isinstance(result, ParsedReminder)
        assert len(result.text) == length
    else:
        assert isinstance(result, ParseError)
        assert result.kind == expect_kind
