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
from aiogram.types import ErrorEvent, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_settings
from ..db import User, list_completed, list_notes, list_reminders, list_todos
from ..services import (
    NoteError,
    ParseError,
    TodoError,
    cancel_reminder_scenario,
    create_note_scenario,
    create_reminder_scenario,
    create_todo_scenario,
    set_timezone_scenario,
)
from .formatter import (
    format_cancel_outcome,
    format_completed_list,
    format_note_confirmation,
    format_note_error,
    format_note_list,
    format_parse_error,
    format_reminder_confirmation,
    format_reminder_list,
    format_todo_confirmation,
    format_todo_error,
    format_todo_list,
)
from .keyboards import (
    completed_keyboard,
    notes_keyboard,
    reminders_keyboard,
    todos_keyboard,
)

__all__ = [
    "PrivateOnly",
    "cmd_cancel",
    "cmd_completed",
    "cmd_help",
    "cmd_note",
    "cmd_notes",
    "cmd_remind",
    "cmd_reminders",
    "cmd_start",
    "cmd_timezone",
    "cmd_todo",
    "cmd_todos",
    "ensure_user",
    "handle_db_error",
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

# Тексты ответов этапа 3/4 (часовой пояс, отмена, заметки, задачи, ошибки БД).
_TIMEZONE_HINT = "Укажите часовой пояс в формате IANA.\nПример: /timezone Europe/Moscow"
_TIMEZONE_UNKNOWN = "Неизвестный пояс. Используйте формат IANA, например Europe/Moscow."
_TIMEZONE_OK = "Часовой пояс изменён на {timezone}."
_CANCEL_HINT = "Укажите id напоминания. Пример: /cancel 42"
_DB_ERROR_TEXT = "Не удалось выполнить действие. Попробуйте ещё раз немного позже."


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


# --- Phase 3/4 commands (Task 20): timezone, cancel, notes, todos ---


def _command_argument(text: str | None) -> str:
    """Возвращает аргумент команды — текст после первого слова.

    ``/timezone Europe/Moscow`` → ``"Europe/Moscow"``;
    ``/cancel@MyBot 42`` → ``"42"`` (суффикс имени бота остаётся в первом слове);
    команда без аргумента → ``""``.

    Args:
        text: ``message.text`` командного сообщения.

    Returns:
        Часть команды после команды (без ведущего слова), либо пустая строка.
    """
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1]


async def cmd_timezone(message: Message) -> None:
    """Команда ``/timezone <IANA>``: меняет часовой пояс пользователя.

    Пустой аргумент → подсказка формата; иначе :func:`set_timezone_scenario`
    (валидация IANA до БД): ``True`` → подтверждение, ``False`` → отказ.
    """
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        timezone = _command_argument(message.text).strip()
        if not timezone:
            await message.answer(_TIMEZONE_HINT)
            return

        updated = await set_timezone_scenario(
            session=session,
            telegram_user_id=user.telegram_user_id,
            timezone=timezone,
            now=now,
        )
        if updated:
            await message.answer(_TIMEZONE_OK.format(timezone=timezone))
        else:
            await message.answer(_TIMEZONE_UNKNOWN)


async def cmd_cancel(message: Message) -> None:
    """Команда ``/cancel <id>``: отменяет напоминание и отвечает исходом.

    Нечисловой или пустой аргумент → подсказка; иначе
    :func:`cancel_reminder_scenario` (изоляция по владельцу) →
    :func:`format_cancel_outcome`.
    """
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        raw_id = _command_argument(message.text).strip()
        try:
            reminder_id = int(raw_id)
        except ValueError:
            await message.answer(_CANCEL_HINT)
            return

        outcome = await cancel_reminder_scenario(
            session=session,
            telegram_user_id=user.telegram_user_id,
            reminder_id=reminder_id,
        )
        await message.answer(format_cancel_outcome(outcome))


async def cmd_note(message: Message) -> None:
    """Команда ``/note <текст>``: создаёт заметку или отвечает ошибкой валидации."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        raw = _command_argument(message.text)
        result = await create_note_scenario(
            session=session,
            telegram_user_id=user.telegram_user_id,
            raw=raw,
            now=now,
        )
        if isinstance(result, NoteError):
            await message.answer(format_note_error(result))
            return
        await message.answer(format_note_confirmation(result))


async def cmd_notes(message: Message) -> None:
    """Команда ``/notes``: список заметок с inline-клавиатурой удаления."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        notes = await list_notes(session=session, user_id=user.telegram_user_id)
        keyboard = notes_keyboard([n.id for n in notes])
        await message.answer(
            format_note_list(notes, user.timezone),
            reply_markup=keyboard,
        )


async def cmd_todo(message: Message) -> None:
    """Команда ``/todo [<срок>] | <текст>``: создаёт задачу или ошибку валидации."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        raw = _command_argument(message.text)
        result = await create_todo_scenario(
            session=session,
            telegram_user_id=user.telegram_user_id,
            raw=raw,
            now=now,
            timezone=user.timezone,
        )
        if isinstance(result, TodoError):
            await message.answer(format_todo_error(result))
            return
        await message.answer(format_todo_confirmation(result, user.timezone))


async def cmd_todos(message: Message) -> None:
    """Команда ``/todos``: список активных задач с клавиатурой (срок/удалить)."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        todos = await list_todos(session=session, user_id=user.telegram_user_id)
        keyboard = todos_keyboard([t.id for t in todos])
        await message.answer(
            format_todo_list(todos, user.timezone),
            reply_markup=keyboard,
        )


async def cmd_completed(message: Message) -> None:
    """Команда ``/completed``: список выполненных задач с клавиатурой удаления."""
    async with _session_factory() as session:  # type: ignore[misc]
        now = datetime.now(UTC)
        user = await ensure_user(
            session=session,
            telegram_user_id=message.from_user.id,
            timezone=get_settings().DEFAULT_TIMEZONE,
            now=now,
        )

        todos = await list_completed(
            session=session,
            user_id=user.telegram_user_id,
        )
        keyboard = completed_keyboard([t.id for t in todos])
        await message.answer(
            format_completed_list(todos, user.timezone),
            reply_markup=keyboard,
        )


# --- Centralized DB errors-handler (Task 20): SQLAlchemyError only ---


async def handle_db_error(event: ErrorEvent) -> None:
    """Централизованный errors-handler: ловит только :class:`SQLAlchemyError`.

    Неперехваченные исключения иных типов пропускаются дальше (ранний возврат).
    Для ошибок БД логируется уровень ``ERROR`` с ``user_id`` из update и кодом
    ошибки (имя класса исключения) — без текста записи и без токена — и
    отправляется единый текст-ответ. Откат транзакции не дублируется: его
    выполнил репозиторий до re-raise, а сессия обработчика к этому моменту уже
    закрыта.

    Args:
        event: aiogram ``ErrorEvent`` (``event.exception``, ``event.update``).
    """
    exc = event.exception
    if not isinstance(exc, SQLAlchemyError):
        return

    update = event.update
    logger.error(
        "database error in update",
        extra={
            "user_id": _user_id_from_update(update),
            "error_code": _error_code(exc),
        },
    )

    message = _message_from_update(update)
    if message is not None:
        await message.answer(_DB_ERROR_TEXT)


def _message_from_update(update: object) -> Message | None:
    """Извлекает ответное сообщение из update (message или callback_query).

    Args:
        update: aiogram ``Update`` из ``ErrorEvent``.

    Returns:
        Сообщение для ответа, либо ``None`` (update без message/callback).
    """
    message = getattr(update, "message", None)
    if message is not None:
        return message
    callback = getattr(update, "callback_query", None)
    if callback is not None:
        return getattr(callback, "message", None)
    return None


def _user_id_from_update(update: object) -> int | None:
    """Извлекает Telegram id из update (callback или message).

    Args:
        update: aiogram ``Update`` из ``ErrorEvent``.

    Returns:
        ``from_user.id`` из callback/message, либо ``None``.
    """
    callback = getattr(update, "callback_query", None)
    if callback is not None:
        from_user = getattr(callback, "from_user", None)
        if from_user is not None:
            return getattr(from_user, "id", None)
    message = _message_from_update(update)
    if message is not None:
        from_user = getattr(message, "from_user", None)
        if from_user is not None:
            return getattr(from_user, "id", None)
    return None


def _error_code(exc: SQLAlchemyError) -> str:
    """Безопасный код ошибки БД (имя класса — без текста записи и токена).

    Args:
        exc: перехваченная ошибка SQLAlchemy.

    Returns:
        Имя класса исключения (напр. ``"OperationalError"``).
    """
    return type(exc).__name__
