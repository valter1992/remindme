"""Клетка worker — бесконечный цикл доставки просроченных напоминаний.

Один фоновый процесс в одном event loop рядом с long polling: при старте один раз
возвращаются зависшие ``sending``-записи (:func:`recover_stuck_sending`), затем
цикл опрашивает просроченные ``scheduled``-напоминания, атомарно захватывает
каждое (:func:`claim_for_sending`), отправляет текст
(:func:`format_notification` через ``bot.send_message``) и фиксирует результат:
успех → :func:`mark_sent`; блокировка бота (:class:`TelegramForbiddenError`) →
немедленный :func:`mark_failed`; иная ошибка Telegram
(:class:`TelegramAPIError`) → :func:`record_send_failure` (расписание 30/120/600,
``failed`` на 4-й неудаче). Одна итерация = одна сессия; токен и текст записи не
логируются.

Тестируемый шаг одной итерации (:func:`_deliver_due`, :func:`_run_iteration`) и
startup-recovery (:func:`_run_recovery`) выделены отдельно — детерминированные
тесты вызывают их напрямую без реального ``asyncio.sleep``. ``datetime.now``
допустимо в цикле worker (фиксация ``now`` на каждой итерации) — и нигде больше.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..bot import format_notification
from ..db import (
    Reminder,
    claim_for_sending,
    find_due_reminders,
    mark_failed,
    mark_sent,
    record_send_failure,
    recover_stuck_sending,
)

__all__ = ["run_reminder_worker"]

logger = logging.getLogger(__name__)

# Порог «зависания» захваченной sending-записи при стартовом recovery, секунд.
_STALE_AFTER_SECONDS = 60


async def run_reminder_worker(
    bot: Bot,
    session_factory: async_sessionmaker,
    poll_interval: int,
) -> None:
    """Бесконечный цикл доставки просроченных напоминаний.

    При старте один раз восстанавливает зависшие ``sending``-записи (старше
    порога), затем зацикливается: засыпает на ``poll_interval``, фиксирует
    aware-UTC ``now``, открывает одну сессию и доставляет все просроченные
    напоминания текущего тика. Запускается как asyncio-задача рядом с polling.

    Args:
        bot: экземпляр aiogram ``Bot`` для отправки уведомлений.
        session_factory: фабрика async-сессий (callable → async context manager).
        poll_interval: интервал опроса, секунд (положительный).
    """
    await _run_recovery(session_factory, datetime.now(UTC))

    while True:
        await asyncio.sleep(poll_interval)
        await _run_iteration(bot, session_factory, datetime.now(UTC))


async def _run_recovery(
    session_factory: async_sessionmaker,
    now: datetime,
) -> int:
    """Startup-recovery: один ``recover_stuck_sending`` в отдельной сессии.

    Выполняется ровно один раз до основного цикла; зависшие ``sending``-записи
    (``locked_at_utc`` старше порога) возвращаются в ``scheduled`` и достаются
    обычным циклом в следующем тике.

    Args:
        session_factory: фабрика async-сессий.
        now: aware-UTC момент старта.

    Returns:
        Количество возвращённых в ``scheduled`` зависших записей (``rowcount``).
    """
    async with session_factory() as session:
        return await recover_stuck_sending(session, now, _STALE_AFTER_SECONDS)


async def _run_iteration(
    bot: Bot,
    session_factory: async_sessionmaker,
    now: datetime,
) -> None:
    """Один тик цикла доставки: одна сессия → доставка всех просроченных записей.

    Args:
        bot: экземпляр aiogram ``Bot``.
        session_factory: фабрика async-сессий.
        now: aware-UTC момент текущей итерации.
    """
    async with session_factory() as session:
        await _deliver_due(bot, session, now)


async def _deliver_due(
    bot: Bot,
    session,  # AsyncSession
    now: datetime,
) -> None:
    """Доставка всех просроченных напоминаний в рамках одной открытой сессии.

    Ошибка одной записи не прерывает обработку остальных: Telegram-ошибки
    фиксируются через ``mark_failed``/``record_send_failure``, неожидаемая ошибка
    БД логируется ERROR (без текста записи и токена) — и цикл продолжается.

    Args:
        bot: экземпляр aiogram ``Bot``.
        session: открытая ``AsyncSession``.
        now: aware-UTC момент итерации.
    """
    due = await find_due_reminders(session, now)

    for reminder in due:
        await _deliver_one(bot, session, reminder, now)


async def _deliver_one(
    bot: Bot,
    session,  # AsyncSession
    reminder: Reminder,
    now: datetime,
) -> None:
    """Доставка одного напоминания: атомарный захват → отправка → фиксация.

    ``claim_for_sending`` фильтрует ``status='scheduled'``: отменённые и уже
    захваченные записи пропускаются. Порядок обработки ошибок важен:
    :class:`TelegramForbiddenError` (подкласс :class:`TelegramAPIError`) ловится
    первым и приводит к немедленному ``failed`` без повторов.

    Args:
        bot: экземпляр aiogram ``Bot``.
        session: открытая ``AsyncSession``.
        reminder: просроченное напоминание (status='scheduled').
        now: aware-UTC момент итерации (база отсрочки при неудаче).
    """
    try:
        if not await claim_for_sending(session, reminder.id, now):
            return

        text = format_notification(reminder)

        started = time.monotonic()
        try:
            await bot.send_message(chat_id=reminder.user_id, text=text)
        except TelegramForbiddenError:
            await mark_failed(session, reminder.id)
            logger.warning(
                "reminder delivery blocked by user",
                extra={"user_id": reminder.user_id},
            )
            return
        except TelegramAPIError as error:
            failed = await record_send_failure(session, reminder.id, now)
            logger.warning(
                "reminder delivery transient failure",
                extra={
                    "user_id": reminder.user_id,
                    "attempt_count": reminder.attempt_count,
                    "failed": failed,
                    "duration_s": round(time.monotonic() - started, 3),
                    "error": str(error),
                },
            )
            return

        await mark_sent(session, reminder.id, now)
    except SQLAlchemyError:
        logger.error(
            "reminder delivery db error",
            extra={"user_id": reminder.user_id},
        )
