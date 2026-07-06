"""Тесты клетки bot: форматтеры (``formatter.py``).

Контракт-тесты проверяют форму фасада (13 функций доступны из
``remindme.bot``) и их сигнатуры; logic-тесты — формат локальной строки,
текст уведомления, подтверждения/списки напоминаний, заметок и задач, а также
тексты ошибок по ``kind`` (включая отсутствие ``invalid_format`` у
``format_todo_error``). Все функции чистые — объекты моделей строятся в памяти
(достаточно читаемых полей), БД и Telegram не нужны.
"""

import pytest

from remindme import bot as bot_facade
from remindme.bot import (
    format_cancel_outcome,
    format_completed_list,
    format_note_confirmation,
    format_note_error,
    format_note_list,
    format_notification,
    format_parse_error,
    format_reminder_confirmation,
    format_reminder_list,
    format_todo_confirmation,
    format_todo_error,
    format_todo_list,
    to_local_string,
)
from remindme.db import CancelOutcome, Note, Reminder, Todo
from remindme.services import NoteError, ParseError, TodoError

_MOSCOW = "Europe/Moscow"
_UTC_ISO = "2026-06-24T15:00:00+00:00"  # 15:00 UTC -> 18:00 Москвы

_EXPECTED = {
    "to_local_string",
    "format_notification",
    "format_parse_error",
    "format_cancel_outcome",
    "format_reminder_confirmation",
    "format_reminder_list",
    "format_note_confirmation",
    "format_note_list",
    "format_note_error",
    "format_todo_confirmation",
    "format_todo_list",
    "format_completed_list",
    "format_todo_error",
}


# --- Контракт-тесты: форма фасада и API ---


def test_facade_exports_all_formatters() -> None:
    """Фасад экспортирует все 13 форматтеров через ``__all__``."""
    assert _EXPECTED.issubset(set(bot_facade.__all__))
    for name in _EXPECTED:
        assert callable(getattr(bot_facade, name))


def test_to_local_string_signature() -> None:
    """``to_local_string(utc_iso, timezone)``."""
    params = to_local_string.__code__.co_varnames[
        : to_local_string.__code__.co_argcount
    ]
    assert params == ("utc_iso", "timezone")


def test_format_notification_signature() -> None:
    """``format_notification(reminder)``."""
    params = format_notification.__code__.co_varnames[
        : format_notification.__code__.co_argcount
    ]
    assert params == ("reminder",)


def test_format_reminder_confirmation_takes_timezone() -> None:
    """``format_reminder_confirmation(reminder, timezone)``."""
    params = format_reminder_confirmation.__code__.co_varnames[
        : format_reminder_confirmation.__code__.co_argcount
    ]
    assert params == ("reminder", "timezone")


# --- Logic-тесты: to_local_string ---


def test_to_local_string_moscow_offset() -> None:
    """15:00 UTC → 18:00 Москвы → «24.06.2026 в 18:00»."""
    assert to_local_string(_UTC_ISO, _MOSCOW) == "24.06.2026 в 18:00"


def test_to_local_string_utc_zone() -> None:
    """Целевая зона UTC оставляет момент без сдвига."""
    assert to_local_string(_UTC_ISO, "UTC") == "24.06.2026 в 15:00"


def test_to_local_string_fixed_format() -> None:
    """Формат ровно «DD.MM.YYYY в HH:MM» (с разделителем «в»)."""
    assert to_local_string("2026-01-02T03:04:00+00:00", "UTC") == "02.01.2026 в 03:04"


# --- Logic-тесты: format_notification ---


def test_format_notification() -> None:
    """«Напоминание\nX»."""
    reminder = Reminder(text="X", remind_at_utc=_UTC_ISO)
    assert format_notification(reminder) == "Напоминание\nX"


# --- Logic-тесты: напоминания ---


def test_format_reminder_confirmation_has_iana_suffix() -> None:
    """Подтверждение содержит локальное время и суффикс (IANA)."""
    reminder = Reminder(text="Позвонить", remind_at_utc=_UTC_ISO)
    text = format_reminder_confirmation(reminder, _MOSCOW)
    assert text.startswith("Напоминание создано\n")
    assert "Позвонить" in text
    assert "24.06.2026 в 18:00 (Europe/Moscow)" in text


def test_format_reminder_list_empty() -> None:
    """Пустой список — сообщение об отсутствии напоминаний."""
    assert format_reminder_list([], _MOSCOW) == "Нет напоминаний."


def test_format_reminder_list_filled() -> None:
    """Заполненный список нумеруется и содержит локальное время и текст."""
    reminders = [
        Reminder(text="Первое", remind_at_utc="2026-06-24T15:00:00+00:00"),
        Reminder(text="Второе", remind_at_utc="2026-06-25T06:00:00+00:00"),
    ]
    text = format_reminder_list(reminders, _MOSCOW)
    lines = text.split("\n")
    assert lines[0] == "1. 24.06.2026 в 18:00 — Первое"
    assert lines[1] == "2. 25.06.2026 в 09:00 — Второе"


# --- Logic-тесты: format_parse_error ---


@pytest.mark.parametrize(
    "kind",
    [
        "invalid_format",
        "past",
        "horizon_exceeded",
        "empty_text",
        "date_not_exist",
        "text_too_long",
        "nonexistent_time",
        "ambiguous_time",
    ],
)
def test_format_parse_error_each_kind(kind: str) -> None:
    """Каждый ParseError.kind отображается в непустой текст."""
    assert format_parse_error(ParseError(kind=kind))


def test_format_parse_error_dst_texts() -> None:
    """DST-варианты описаны переводом часов."""
    assert "перевод часов" in format_parse_error(ParseError(kind="nonexistent_time"))
    assert "неоднозначно" in format_parse_error(ParseError(kind="ambiguous_time"))


# --- Logic-тесты: format_cancel_outcome ---


@pytest.mark.parametrize(
    ("kind", "substring"),
    [
        ("cancelled", "отменено"),
        ("not_found", "не найдено"),
        ("already_sending", "уже отправляется"),
    ],
)
def test_format_cancel_outcome_each_kind(kind: str, substring: str) -> None:
    """Каждый CancelOutcome.kind отображается в осмысленный текст."""
    assert substring in format_cancel_outcome(CancelOutcome(kind=kind))


# --- Logic-тесты: заметки ---


def test_format_note_confirmation() -> None:
    """Подтверждение заметки содержит её текст."""
    note = Note(text="Идея дня")
    assert format_note_confirmation(note).endswith("Идея дня")


def test_format_note_list_empty() -> None:
    """Пустой список заметок — сообщение об отсутствии."""
    assert format_note_list([], _MOSCOW) == "Нет заметок."


def test_format_note_list_filled() -> None:
    """Список заметок: локальное время создания + текст."""
    notes = [Note(text="Первая заметка", created_at_utc=_UTC_ISO)]
    assert format_note_list(notes, _MOSCOW) == "1. 24.06.2026 в 18:00 — Первая заметка"


@pytest.mark.parametrize("kind", ["empty_text", "text_too_long"])
def test_format_note_error_each_kind(kind: str) -> None:
    """Допустимые NoteError.kind отображаются в непустой текст."""
    assert format_note_error(NoteError(kind=kind))


# --- Logic-тесты: задачи ---


def test_format_todo_confirmation_without_due() -> None:
    """Подтверждение задачи без срока содержит «без срока»."""
    todo = Todo(text="Купить хлеб", due_at_utc=None)
    text = format_todo_confirmation(todo, _MOSCOW)
    assert "Купить хлеб" in text
    assert "без срока" in text


def test_format_todo_confirmation_with_due() -> None:
    """Подтверждение задачи со сроком содержит локальное время срока."""
    todo = Todo(text="Сдать отчёт", due_at_utc=_UTC_ISO)
    text = format_todo_confirmation(todo, _MOSCOW)
    assert "Сдать отчёт" in text
    assert "24.06.2026 в 18:00" in text


def test_format_todo_list_empty() -> None:
    """Пустой список задач — сообщение об отсутствии."""
    assert format_todo_list([], _MOSCOW) == "Нет задач."


def test_format_todo_list_filled_with_and_without_due() -> None:
    """Список задач: со сроком — локальное время, без срока — «без срока»."""
    todos = [
        Todo(text="Со сроком", due_at_utc=_UTC_ISO),
        Todo(text="Без срока", due_at_utc=None),
    ]
    text = format_todo_list(todos, _MOSCOW)
    lines = text.split("\n")
    assert lines[0] == "1. [24.06.2026 в 18:00] Со сроком"
    assert lines[1] == "2. [без срока] Без срока"


def test_format_completed_list_empty() -> None:
    """Пустой список выполненных — сообщение об отсутствии."""
    assert format_completed_list([], _MOSCOW) == "Нет выполненных задач."


def test_format_completed_list_filled() -> None:
    """Список выполненных: локальное время завершения + текст."""
    todos = [Todo(text="Готово", completed_at_utc=_UTC_ISO)]
    assert format_completed_list(todos, _MOSCOW) == "1. 24.06.2026 в 18:00 — Готово"


@pytest.mark.parametrize("kind", ["empty_text", "text_too_long"])
def test_format_todo_error_each_kind(kind: str) -> None:
    """Допустимые TodoError.kind отображаются в непустой текст."""
    assert format_todo_error(TodoError(kind=kind))


def test_format_todo_error_without_invalid_format() -> None:
    """``format_todo_error`` НЕ содержит ветки ``invalid_format``.

    Терпимый парсер задачи не выдаёт ``invalid_format``; форматтер задачи
    отображает только ``empty_text``/``text_too_long``.
    """
    for kind in ("empty_text", "text_too_long"):
        assert format_todo_error(TodoError(kind=kind))

    # Нечаянный invalid_format не даёт текста про «разобрать время» (ParseError).
    assert "разобрать время" not in format_todo_error(TodoError(kind="invalid_format"))
