"""Клетка services — чистые парсеры и сценарии RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: детерминированный
парсер напоминаний (:func:`parse_remind_time`, :class:`ParsedReminder`,
:class:`ParseError`); сценарии напоминаний/заметок/задач и типы ошибок
наполняются в последующих задачах клетки.
"""

from .parser import ParsedReminder, ParseError, parse_remind_time

__all__ = ["ParseError", "ParsedReminder", "parse_remind_time"]
