"""Сценарии напоминаний — оркестрация парсер → репозиторий.

Сценарии соединяют чистый детерминированный парсер :func:`parse_remind_time` с
репозиториями напоминаний и пользователя. Текущий момент фиксируется снаружи
(параметр ``now``); ``datetime.now()`` внутри модуля не вызывается. При ошибке
парсинга запись в БД не создаётся — :class:`ParseError` возвращается как
типизированный результат без сохранения. Валидация IANA-зоны в
:func:`set_timezone_scenario` также выполняется до обращения к БД.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import (
    CancelOutcome,
    Reminder,
    cancel_reminder,
    create_reminder,
    set_user_timezone,
)
from .parser import ParseError, parse_remind_time

__all__ = [
    "cancel_reminder_scenario",
    "create_reminder_scenario",
    "set_timezone_scenario",
]

logger = logging.getLogger(__name__)


async def create_reminder_scenario(
    session: AsyncSession,
    telegram_user_id: int,
    raw: str,
    now: datetime,
    timezone: str,
    default_time: str,
) -> Reminder | ParseError:
    """Создаёт напоминание из текста команды/фразы или возвращает ошибку парсинга.

    Сначала выполняется чистый разбор ``raw`` через :func:`parse_remind_time`. При
    :class:`ParseError` функция возвращает её без обращения к БД — запись не
    создаётся. При успехе делегирует :func:`create_reminder` с разобранными
    ``remind_at_utc``/``text``.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        raw: исходный текст команды (``/remind …``) или фразы («напомни …»).
        now: текущий aware-UTC момент (фиксируется handler'ом).
        timezone: IANA-зона пользователя.
        default_time: время по умолчанию «ЧЧ:ММ» для «сегодня/завтра» без явного.

    Returns:
        Сохранённый :class:`Reminder` при успехе либо :class:`ParseError` (без
        обращения к БД).
    """
    parsed = parse_remind_time(raw, now, timezone, default_time)
    if isinstance(parsed, ParseError):
        logger.info(
            "reminder parse rejected",
            extra={"user_id": telegram_user_id, "kind": parsed.kind},
        )
        return parsed

    return await create_reminder(
        session=session,
        telegram_user_id=telegram_user_id,
        text=parsed.text,
        remind_at_utc=parsed.remind_at_utc,
        now=now,
    )


async def set_timezone_scenario(
    session: AsyncSession,
    telegram_user_id: int,
    timezone: str,
    now: datetime,
) -> bool:
    """Устанавливает часовой пояс пользователя после валидации IANA.

    Сначала зона проверяется через :class:`ZoneInfo`: некорректное значение
    возвращает ``False`` без обращения к БД. При валидной зоне делегирует
    :func:`set_user_timezone` (``rowcount == 1`` отличает обновление от
    отсутствующего пользователя).

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: PRIMARY KEY пользователя.
        timezone: кандидат IANA-зоны.
        now: текущий aware-UTC момент для ``updated_at_utc``.

    Returns:
        ``True``, если зона валидна И обновлён ровно один пользователь; ``False``
        при некорректной зоне или отсутствии пользователя.
    """
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.info("timezone rejected", extra={"user_id": telegram_user_id})
        return False

    return await set_user_timezone(
        session=session,
        telegram_user_id=telegram_user_id,
        timezone=timezone,
        now=now,
    )


async def cancel_reminder_scenario(
    session: AsyncSession,
    telegram_user_id: int,
    reminder_id: int,
) -> CancelOutcome:
    """Отменяет напоминание пользователя, делегируя :func:`cancel_reminder`.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец.
        reminder_id: идентификатор напоминания.

    Returns:
        :class:`CancelOutcome` репозитория (``cancelled`` / ``already_sending`` /
        ``not_found``) — существование чужих записей не раскрывается.
    """
    return await cancel_reminder(
        session=session,
        reminder_id=reminder_id,
        user_id=telegram_user_id,
    )
