"""Клетка worker — фоновый цикл доставки напоминаний RemindMe.

Фасадный модуль клетки экспонирует собственные сущности: бесконечный цикл
доставки :func:`run_reminder_worker` (startup-recovery зависших ``sending``,
атомарный захват, эскалация до ``failed``, расписание повторов 30/120/600).
Зависит от ``format_notification`` (клетка bot) и delivery-примитивов репозитория
(клетка db).
"""

from .notifications import run_reminder_worker

__all__: list[str] = ["run_reminder_worker"]
