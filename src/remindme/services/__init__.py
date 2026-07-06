"""Клетка services — чистые парсеры и сценарии RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: детерминированный
парсер напоминаний (:func:`parse_remind_time`, :class:`ParsedReminder`,
:class:`ParseError`) и сценарии напоминаний (:func:`create_reminder_scenario`,
:func:`set_timezone_scenario`, :func:`cancel_reminder_scenario`); сценарии
заметок/задач и типы ошибок наполняются в последующих задачах клетки.
"""

from .parser import ParsedReminder, ParseError, parse_remind_time
from .reminders import (
    cancel_reminder_scenario,
    create_reminder_scenario,
    set_timezone_scenario,
)

__all__ = [
    "ParseError",
    "ParsedReminder",
    "cancel_reminder_scenario",
    "create_reminder_scenario",
    "parse_remind_time",
    "set_timezone_scenario",
]
