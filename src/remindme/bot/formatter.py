"""Форматтеры ответов и уведомлений Telegram-бота.

Чистые функции без побочных эффектов: преобразование aware-UTC момента в
локальную строку пользователя (:func:`to_local_string`), текст уведомления для
worker (:func:`format_notification`), подтверждения/списки/ошибки напоминаний,
заметок и задач. Ни одна функция не обращается к БД или Telegram; текущий момент
приходит аргументом (для форматтеров — уже как ISO 8601 строка из БД).

Локальная строка времени едина для всех форматтеров: формат
``DD.MM.YYYY в HH:MM``; суффикс часового пояса ``(IANA)`` добавляется только в
подтверждении создания напоминания (:func:`format_reminder_confirmation`).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..db import CancelOutcome, Note, Reminder, Todo
from ..services import NoteError, ParseError, TodoError

__all__ = [
    "format_cancel_outcome",
    "format_completed_list",
    "format_notification",
    "format_note_confirmation",
    "format_note_error",
    "format_note_list",
    "format_parse_error",
    "format_reminder_confirmation",
    "format_reminder_list",
    "format_todo_confirmation",
    "format_todo_error",
    "format_todo_list",
    "to_local_string",
]

# Фиксированный человекочитаемый формат локальной строки «DD.MM.YYYY в HH:MM».
_LOCAL_FORMAT = "%d.%m.%Y в %H:%M"

# Тексты отказа парсера по варианту ошибки.
_PARSE_ERROR_TEXTS: dict[str, str] = {
    "invalid_format": (
        "Не удалось разобрать время. "
        "Примеры: «через 10 минут», «завтра в 09:00», «31.12.2026 в 18:00»."
    ),
    "past": "Указанное время уже прошло. Укажите время в будущем.",
    "horizon_exceeded": "Слишком далёкое время (более чем через год).",
    "empty_text": "Текст напоминания пуст.",
    "date_not_exist": "Такой даты не существует. Проверьте день и месяц.",
    "text_too_long": "Текст слишком длинный (максимум 500 символов).",
    "nonexistent_time": (
        "Такого времени не существует в этот день (перевод часов). "
        "Выберите другое время."
    ),
    "ambiguous_time": (
        "Это время неоднозначно из-за перевода часов. Выберите другое время."
    ),
}

# Тексты исхода отмены по варианту.
_CANCEL_OUTCOME_TEXTS: dict[str, str] = {
    "cancelled": "Напоминание отменено.",
    "not_found": "Напоминание не найдено.",
    "already_sending": "Напоминание уже отправляется и не может быть отменено.",
}

# Тексты ошибок валидации заметки (без invalid_format).
_NOTE_ERROR_TEXTS: dict[str, str] = {
    "empty_text": "Текст заметки пуст.",
    "text_too_long": "Текст слишком длинный (максимум 500 символов).",
}

# Тексты ошибок валидации задачи (без invalid_format).
_TODO_ERROR_TEXTS: dict[str, str] = {
    "empty_text": "Текст задачи пуст.",
    "text_too_long": "Текст слишком длинный (максимум 500 символов).",
}


def to_local_string(utc_iso: str, timezone: str) -> str:
    """Преобразует aware-UTC ISO строку из БД в локальную строку пользователя.

    Args:
        utc_iso: момент в формате ISO 8601 UTC (как хранится в БД, напр.
            ``reminder.remind_at_utc``).
        timezone: IANA-зона пользователя.

    Returns:
        Локальная строка даты/времени в фиксированном формате
        ``DD.MM.YYYY в HH:MM``.
    """
    aware = datetime.fromisoformat(utc_iso)
    local = aware.astimezone(ZoneInfo(timezone))
    return local.strftime(_LOCAL_FORMAT)


def format_notification(reminder: Reminder) -> str:
    """Текст уведомления о напоминании для отправки пользователю.

    Args:
        reminder: отправляемое напоминание.

    Returns:
        Строка вида ``«Напоминание\\n<reminder.text>»``.
    """
    return "Напоминание\n" + reminder.text


def format_parse_error(error: ParseError) -> str:
    """Текст отказа по варианту ошибки парсера (шаблон и пример).

    Args:
        error: ошибка парсера.

    Returns:
        Пользовательский текст, соответствующий ``error.kind``.
    """
    return _PARSE_ERROR_TEXTS.get(error.kind, _PARSE_ERROR_TEXTS["invalid_format"])


def format_cancel_outcome(outcome: CancelOutcome) -> str:
    """Текст результата отмены по варианту исхода.

    Args:
        outcome: исход отмены.

    Returns:
        Пользовательский текст, соответствующий ``outcome.kind``.
    """
    return _CANCEL_OUTCOME_TEXTS.get(outcome.kind, _CANCEL_OUTCOME_TEXTS["not_found"])


def format_reminder_confirmation(reminder: Reminder, timezone: str) -> str:
    """Подтверждение создания напоминания с локальным временем и поясом.

    Args:
        reminder: созданное напоминание.
        timezone: IANA-зона пользователя.

    Returns:
        Подтверждение вида «Напоминание создано», текст напоминания и локальное
        время с суффиксом ``(IANA)`` (напр. «24.06.2026 в 12:15 (Europe/Moscow)»).
    """
    local = to_local_string(reminder.remind_at_utc, timezone)
    return f"Напоминание создано\n{reminder.text}\n{local} ({timezone})"


def format_reminder_list(reminders: list[Reminder], timezone: str) -> str:
    """Список активных напоминаний в локальном времени.

    Args:
        reminders: список напоминаний текущего пользователя.
        timezone: IANA-зона пользователя.

    Returns:
        Пронумерованный список (локальное время + текст) либо сообщение об
        отсутствии напоминаний.
    """
    if not reminders:
        return "Нет напоминаний."
    lines = [
        f"{i}. {to_local_string(r.remind_at_utc, timezone)} — {r.text}"
        for i, r in enumerate(reminders, start=1)
    ]
    return "\n".join(lines)


def format_note_confirmation(note: Note) -> str:
    """Подтверждение создания заметки.

    Args:
        note: созданная заметка.

    Returns:
        Подтверждение вида «Заметка создана» и текст заметки.
    """
    return f"Заметка создана:\n{note.text}"


def format_note_list(notes: list[Note], timezone: str) -> str:
    """Список заметок (локальное время создания + текст).

    Args:
        notes: список заметок текущего пользователя.
        timezone: IANA-зона пользователя.

    Returns:
        Пронумерованный список либо сообщение об отсутствии заметок.
    """
    if not notes:
        return "Нет заметок."
    lines = [
        f"{i}. {to_local_string(n.created_at_utc, timezone)} — {n.text}"
        for i, n in enumerate(notes, start=1)
    ]
    return "\n".join(lines)


def format_note_error(error: NoteError) -> str:
    """Текст отказа валидации заметки по варианту ошибки.

    Args:
        error: ошибка валидации заметки.

    Returns:
        Пользовательский текст, соответствующий ``error.kind``.
    """
    return _NOTE_ERROR_TEXTS.get(error.kind, _NOTE_ERROR_TEXTS["empty_text"])


def format_todo_confirmation(todo: Todo, timezone: str) -> str:
    """Подтверждение создания задачи с текстом и сроком.

    Args:
        todo: созданная задача.
        timezone: IANA-зона пользователя.

    Returns:
        Подтверждение вида «Задача создана», текст задачи и срок (локальное
        время или «без срока»).
    """
    due = _due_label(todo, timezone)
    return f"Задача создана:\n{todo.text}\nСрок: {due}"


def format_todo_list(todos: list[Todo], timezone: str) -> str:
    """Список активных задач (срок локально / «без срока» + текст).

    Просроченные активные задачи отображаются (доставки нет).

    Args:
        todos: список активных задач текущего пользователя.
        timezone: IANA-зона пользователя.

    Returns:
        Пронумерованный список либо сообщение об отсутствии задач.
    """
    if not todos:
        return "Нет задач."
    lines = [
        f"{i}. [{_due_label(t, timezone)}] {t.text}"
        for i, t in enumerate(todos, start=1)
    ]
    return "\n".join(lines)


def format_completed_list(todos: list[Todo], timezone: str) -> str:
    """Список выполненных задач (локальное время завершения + текст).

    Args:
        todos: список выполненных задач текущего пользователя.
        timezone: IANA-зона пользователя.

    Returns:
        Пронумерованный список либо сообщение об отсутствии выполненных задач.
    """
    if not todos:
        return "Нет выполненных задач."
    lines = [
        f"{i}. {to_local_string(t.completed_at_utc, timezone)} — {t.text}"  # type: ignore[arg-type]
        for i, t in enumerate(todos, start=1)
    ]
    return "\n".join(lines)


def format_todo_error(error: TodoError) -> str:
    """Текст отказа валидации задачи по варианту ошибки.

    Без варианта ``invalid_format`` (по контракту терпимого парсера задачи).

    Args:
        error: ошибка валидации задачи.

    Returns:
        Пользовательский текст, соответствующий ``error.kind``.
    """
    return _TODO_ERROR_TEXTS.get(error.kind, _TODO_ERROR_TEXTS["empty_text"])


def _due_label(todo: Todo, timezone: str) -> str:
    """Локальная метка срока задачи или «без срока»."""
    if todo.due_at_utc is None:
        return "без срока"
    return to_local_string(todo.due_at_utc, timezone)
