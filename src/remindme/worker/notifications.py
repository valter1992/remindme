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

Неожидаемая ошибка БД (``SQLAlchemyError`` из claim/mark_*) обрывает текущий тик:
сессия остаётся в failed-состоянии, и продолжать тик бессмысленно —
``run_reminder_worker`` ловит ошибку, логирует её и продолжает цикл на свежей
сессии следующего тика, так что просроченные записи доставляются без потерь.

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
    напоминания текущего тика. Ошибка БД одного тика логируется ERROR (без текста
    записи и токена) и не убивает цикл: следующий тик открывает свежую сессию и
    дообрабатывает оставшиеся просроченные записи. Запускается как asyncio-задача
    рядом с polling.

    Args:
        bot: экземпляр aiogram ``Bot`` для отправки уведомлений.
        session_factory: фабрика async-сессий (callable → async context manager).
        poll_interval: интервал опроса, секунд (положительный).
    """
    await _run_recovery(session_factory, datetime.now(UTC))

    while True:
        await asyncio.sleep(poll_interval)
        try:
            await _run_iteration(bot, session_factory, datetime.now(UTC))
        except SQLAlchemyError:
            # Сессия тика осталась в failed-состоянии (см. _deliver_due): тик
            # обрывается, но сам цикл живёт — следующий тик откроет свежую сессию.
            logger.error("reminder worker tick db error")


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

    Telegram-ошибки одной записи не прерывают обработку остальных:
    фиксируются через ``mark_failed``/``record_send_failure``. Неожидаемая ошибка
    БД (``SQLAlchemyError`` из claim/mark_*) НЕ проглатывается: после неё сессия
    остаётся в failed-состоянии (повторный ``execute`` поднимёт
    ``PendingRollbackError``), поэтому исключение поднимается наружу и обрывает
    тик. ``run_reminder_worker`` ловит его, логирует ERROR (без текста записи и
    токена) и продолжает цикл на свежей сессии следующего тика.

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
    первым и приводит к немедленному ``failed`` без повторов. ``user_id``
    фиксируется в локальную переменную заранее: при ошибке БД сессия остаётся в
    failed-состоянии, ORM-атрибуты записи становятся недоступны, а лог нужен и в
    этом случае. Ошибка БД (``SQLAlchemyError`` из claim/mark_*) здесь НЕ
    ловится как мягкий отказ — она логируется и поднимается, обрывая тик
    (см. :func:`_deliver_due`).

    Args:
        bot: экземпляр aiogram ``Bot``.
        session: открытая ``AsyncSession``.
        reminder: просроченное напоминание (status='scheduled').
        now: aware-UTC момент итерации (база отсрочки при неудаче).
    """
    user_id = reminder.user_id

    try:
        if not await claim_for_sending(session, reminder.id, now):
            return

        text = format_notification(reminder)

        started = time.monotonic()
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except TelegramForbiddenError:
            await mark_failed(session, reminder.id)
            logger.warning(
                "reminder delivery blocked by user",
                extra={"user_id": user_id},
            )
            return
        except TelegramAPIError as error:
            failed = await record_send_failure(session, reminder.id, now)
            logger.warning(
                "reminder delivery transient failure",
                extra={
                    "user_id": user_id,
                    "failed": failed,
                    "duration_s": round(time.monotonic() - started, 3),
                    # ``str(error)`` — сообщение Telegram об ошибке; текст записи и
                    # токен сюда не попадают.
                    "error": str(error),
                },
            )
            return

        await mark_sent(session, reminder.id, now)
    except SQLAlchemyError:
        # Ошибка БД (claim/mark_*) не маскируется под мягкий отказ: после неё
        # сессия остаётся в failed-состоянии, и тихий возврат привёл бы к
        # обрыву доставки остальных записей тика (PendingRollbackError). Логируем
        # контекст (``user_id`` зафиксирован заранее) и поднимаем исключение —
        # ``run_reminder_worker`` обрывает тик и продолжит цикл на свежей сессии.
        logger.error("reminder delivery db error", extra={"user_id": user_id})
        raise
