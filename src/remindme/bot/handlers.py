"""Обработчики клетки bot — фильтр приватных чатов и команды этапа 1.

Каждый пишущий обработчик первым шагом фиксирует aware-UTC ``now``
(:func:`datetime.now` допустимо в обработчиках и в цикле worker — и нигде больше)
и передаёт его в :func:`ensure_user`. Фабрика сессий замыкается на уровне модуля
(``_session_factory``; устанавливается фасадом :func:`register_handlers` один раз
при запуске) и переиспользуется всеми пишущими обработчиками. Обработчики групп и
неизвестных команд не обращаются к БД. :func:`ensure_user` идемпотентно
регистрирует пользователя, восстанавливаясь из ``IntegrityError`` при конкурентной
регистрации повторным ``SELECT``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_settings
from ..db import User, list_reminders
from ..services import ParseError, create_reminder_scenario
from .formatter import (
    format_parse_error,
    format_reminder_confirmation,
    format_reminder_list,
)
from .keyboards import reminders_keyboard

__all__ = [
    "PrivateOnly",
    "cmd_help",
    "cmd_remind",
    "cmd_reminders",
    "cmd_start",
    "ensure_user",
    "handle_group",
    "handle_remind_phrase",
    "handle_text",
    "handle_unknown_command",
]

logger = logging.getLogger(__name__)

# Фабрика сессий, замыкаемая регистратором обработчиков; ``None`` до запуска.
# В тестах подменяется на контекстный менеджер над общей тестовой сессией.
_session_factory: async_sessionmaker[AsyncSession] | None = None

# Тексты ответов этапа 1.
_GREETING = (
    "Привет! Я RemindMe — бот-напоминалка.\n"
    "Создавайте напоминания, заметки и задачи.\n"
    "Введите /help, чтобы увидеть список команд."
)
_HELP = (
    "Команды:\n"
    "/remind <когда> | <текст> — напоминание\n"
    "напомни <когда> <текст> — напоминание фразой\n"
    "/reminders — мои напоминания\n"
    "/cancel <id> — отменить напоминание\n"
    "/note <текст> — заметка\n"
    "/notes — мои заметки\n"
    "/todo [<срок>] | <текст> — задача\n"
    "/todos — мои задачи\n"
    "/completed — выполненные задачи\n"
    "/timezone <IANA> — сменить часовой пояс"
)
_UNKNOWN_COMMAND = "Неизвестная команда. Введите /help для списка команд."
_TEXT_HINT = "Я не понял сообщение. Введите /help для списка команд."
_GROUP_UNSUPPORTED = "Я работаю только в личных сообщениях. Напишите мне в личку."

# Гард длины сообщения до разбора (текст ограничен парсером отдельно).
_MAX_MESSAGE_LEN = 1000
_MESSAGE_TOO_LONG = "Сообщение слишком длинное (максимум 1000 символов)."


class PrivateOnly(BaseFilter):
    """Фильтр личных чатов: пропускает только ``chat.type == 'private'``."""

    async def __call__(self, message: Message) -> bool:
        """Возвращает ``True`` для приватного чата.

        Args:
            message: входящее сообщение aiogram.

        Returns:
            ``True``, если сообщение из личного чата; иначе ``False``.
        """
        return message.chat.type == "private"


async def ensure_user(
    session: AsyncSession,
    telegram_user_id: int,
    timezone: str,
    now: datetime,
) -> User:
    """Идемпотентно регистрирует или обновляет пользователя.

    При первом сообщении создаёт ``User`` (``created_at_utc``/``updated_at_utc``
    фиксируются ``now``); при повторном — обновляет только ``updated_at_utc``,
    оставляя ``created_at_utc`` неизменным. Конкурентная регистрация того же
    ``telegram_user_id`` поднимает ``IntegrityError`` (PK/UNIQUE) — функция
    откатывает транзакцию и повторным ``SELECT`` возвращает уже существующую
    запись.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: PRIMARY KEY пользователя (Telegram id).
        timezone: IANA-зона на момент регистрации (``DEFAULT_TIMEZONE``).
        now: текущий aware-UTC момент.

    Returns:
        Существующий или созданный ``User``.
    """
    now_iso = now.isoformat()
    stmt = select(User).where(User.telegram_user_id == telegram_user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user is not None:
        user.updated_at_utc = now_iso
        await session.commit()
        return user

    user = User(
        telegram_user_id=telegram_user_id,
        timezone=timezone,
        created_at_utc=now_iso,
        updated_at_utc=now_iso,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info(
            "user registration race resolved",
            extra={"user_id": telegram_user_id},
        )
        return (await session.execute(stmt)).scalar_one()

    return user


async def _ensure_sender(message: Message) -> None:
    """Регистрирует отправителя сообщения в одной сессии.

    Общий шаг пишущих обработчиков этапа 1: открыть сессию из замкнутой фабрики,
    зафиксировать ``now`` и идемпотентно зарегистрировать пользователя с зоной по
    умолчанию из настроек.

    Args:
        message: входящее сообщение; ``message.from_user.id`` — Telegram id.
    """
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )


async def cmd_start(message: Message) -> None:
    """Команда ``/start``: регистрирует пользователя и приветствует."""
    await _ensure_sender(message)
    await message.answer(_GREETING)


async def cmd_help(message: Message) -> None:
    """Команда ``/help``: регистрирует пользователя и показывает команды."""
    await _ensure_sender(message)
    await message.answer(_HELP)


async def handle_unknown_command(message: Message) -> None:
    """Неизвестная команда: подсказка обратиться к ``/help`` (без БД)."""
    await message.answer(_UNKNOWN_COMMAND)


async def handle_text(message: Message) -> None:
    """Свободный текст в личке: регистрирует пользователя и подсказывает ``/help``."""
    await _ensure_sender(message)
    await message.answer(_TEXT_HINT)


async def handle_group(message: Message) -> None:
    """Сообщения из групп: отказ (без БД); регистрируется последним без фильтра."""
    await message.answer(_GROUP_UNSUPPORTED)


# --- Remind flow (Task 19): /remind, /reminders, «напомни ...» ---


async def _answer_reminder(message: Message) -> None:
    """Общая логика создания напоминания для команды и фразы.

    Шаги: открыть сессию → зафиксировать ``now`` → идемпотентно
    зарегистрировать пользователя → проверить длину сообщения (``>1000`` —
    отказ до разбора) → :func:`create_reminder_scenario` (текст команды/фразы
    передаётся как ``raw`` целиком, разделитель ``|`` разбирает парсер) → при
    :class:`ParseError` ответить текстом ошибки без сохранения, иначе —
    подтверждением с локальным временем и поясом.

    Args:
        message: приватное сообщение; ``message.text`` — команда ``/remind …``
            или фраза «напомни …».
    """
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        timezone = get_settings().DEFAULT_TIMEZONE
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=timezone,
            now=now,
        )

        if len(message.text) > _MAX_MESSAGE_LEN:
            await message.answer(_MESSAGE_TOO_LONG)
            return

        result = await create_reminder_scenario(
            session=session,
            telegram_user_id=user.telegram_user_id,
            raw=message.text,
            now=now,
            timezone=user.timezone,
            default_time=get_settings().DEFAULT_REMINDER_TIME,
        )
        if isinstance(result, ParseError):
            await message.answer(format_parse_error(result))
            return
        await message.answer(format_reminder_confirmation(result, user.timezone))


async def cmd_remind(message: Message) -> None:
    """Команда ``/remind <время> | <текст>``: создаёт напоминание."""
    await _answer_reminder(message)


async def cmd_reminders(message: Message) -> None:
    """Команда ``/reminders``: список активных напоминаний с inline-клавиатурой."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )
        reminders = await list_reminders(
            session=session,
            user_id=user.telegram_user_id,
        )
        keyboard = reminders_keyboard([r.id for r in reminders])
        await message.answer(
            format_reminder_list(reminders, user.timezone),
            reply_markup=keyboard,
        )


async def handle_remind_phrase(message: Message) -> None:
    """Фраза «напомни [мне] …» в личке: создаёт напоминание.

    Регистрируется перед ``handle_text`` с фильтром начала «напомни»
    (``re.IGNORECASE``); здесь применяется та же логика создания, что и в
    :func:`cmd_remind` — разделитель ``|`` и префикс разбирает парсер.
    """
    await _answer_reminder(message)
