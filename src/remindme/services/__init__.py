"""Клетка services — чистые парсеры и сценарии RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: детерминированный
парсер напоминаний (:func:`parse_remind_time`, :class:`ParsedReminder`,
:class:`ParseError`), сценарии напоминаний (:func:`create_reminder_scenario`,
:func:`set_timezone_scenario`, :func:`cancel_reminder_scenario`) и сценарий
заметок (:func:`create_note_scenario`, :class:`NoteError`); сценарии задач и их
типы ошибок наполняются в последующих задачах клетки.
"""

from .notes import NoteError, create_note_scenario
from .parser import ParsedReminder, ParseError, parse_remind_time
from .reminders import (
    cancel_reminder_scenario,
    create_reminder_scenario,
    set_timezone_scenario,
)
from .todos import ParsedTodo, TodoError, create_todo_scenario, parse_todo_input

__all__ = [
    "NoteError",
    "ParseError",
    "ParsedReminder",
    "ParsedTodo",
    "TodoError",
    "cancel_reminder_scenario",
    "create_note_scenario",
    "create_reminder_scenario",
    "create_todo_scenario",
    "parse_remind_time",
    "parse_todo_input",
    "set_timezone_scenario",
]
