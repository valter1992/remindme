"""Клетка bot — слой Telegram-интерфейса RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: форматтеры ответов и
уведомлений (``to_local_string``, ``format_*``). Фильтр приватных чатов,
обработчики команд и сообщений (aiogram), inline-клавиатуры и callback-роутер
наполняются в последующих задачах клетки.
"""

from .formatter import (
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
