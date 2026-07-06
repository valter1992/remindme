"""Репозиторий напоминаний и пользователя — операции по владельцу и статусу.

Каждая функция принимает открытую ``AsyncSession`` и фильтруется одновременно по
владельцу (``user_id``) и/или статусу: поиск/изменение только по ``id`` запрещён.
Timestamp-поля — строки ISO 8601 UTC. Возврат ``rowcount`` отличает «успех» от
«не найдено/не владельцу», не раскрывая чужие данные. При ``SQLAlchemyError``
репозиторий откатывает транзакцию, логирует контекст (без текста записи и токена)
и повторно поднимает исключение.
"""

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Reminder, User

__all__ = [
    "CancelOutcome",
    "cancel_reminder",
    "create_reminder",
    "list_reminders",
    "set_user_timezone",
]

logger = logging.getLogger(__name__)


class CancelOutcome(BaseModel):
    """Различимый исход отмены напоминания.

    Не раскрывает существование чужих записей: ``not_found`` объединяет случаи
    «отсутствует» и «принадлежит другому пользователю».

    Attributes:
        kind: ``cancelled`` — ровно одна запись scheduled→cancelled;
            ``already_sending`` — запись захвачена worker (status='sending');
            ``not_found`` — не найдено или чужая.
    """

    model_config = ConfigDict(kw_only=True)

    kind: str


async def create_reminder(
    session: AsyncSession,
    telegram_user_id: int,
    text: str,
    remind_at_utc: datetime,
    now: datetime,
) -> Reminder:
    """Создаёт напоминание в статусе scheduled и фиксирует транзакцию.

    Начальные значения: ``status='scheduled'``, ``attempt_count=0``,
    ``next_attempt_at_utc=remind_at_utc``.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: владелец (FK на users.telegram_user_id).
        text: текст напоминания.
        remind_at_utc: момент напоминания (aware UTC).
        now: момент создания (aware UTC).

    Returns:
        Сохранённый ``Reminder`` с присвоенным ``id``.

    Raises:
        SQLAlchemyError: при ошибке БД — транзакция откатывается, контекст
            залогирован, исключение поднимается повторно.
    """
    remind_iso = remind_at_utc.isoformat()

    reminder = Reminder(
        user_id=telegram_user_id,
        text=text,
        remind_at_utc=remind_iso,
        status="scheduled",
        attempt_count=0,
        next_attempt_at_utc=remind_iso,
        created_at_utc=now.isoformat(),
    )

    try:
        session.add(reminder)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error(
            "reminder create failed",
            extra={"user_id": telegram_user_id},
        )
        raise

    return reminder


async def list_reminders(session: AsyncSession, user_id: int) -> list[Reminder]:
    """Возвращает активные напоминания пользователя (status='scheduled').

    Args:
        session: открытая ``AsyncSession``.
        user_id: владелец.

    Returns:
        Список не более чем из 20 напоминаний, ближайших по ``remind_at_utc``
        (порядок asc). Только записи текущего пользователя.
    """
    stmt = (
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.status == "scheduled")
        .order_by(Reminder.remind_at_utc.asc())
        .limit(20)
    )
    result = await session.execute(stmt)

    return list(result.scalars().all())


async def cancel_reminder(
    session: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> CancelOutcome:
    """Отменяет напоминание по ``(reminder_id, user_id)`` только из scheduled.

    Атомарный переход scheduled→cancelled: ``WHERE`` фильтрует и владельца, и
    статус; ``rowcount == 1`` означает успех. Если переход не выполнен, выполняется
    уточняющий ``SELECT status`` по ``(id, user_id)`` для различения исхода.

    Args:
        session: открытая ``AsyncSession``.
        reminder_id: идентификатор напоминания.
        user_id: владелец.

    Returns:
        ``CancelOutcome(kind='cancelled')`` при успехе;
        ``kind='already_sending'`` — запись захвачена worker (status='sending');
        ``kind='not_found'`` — не найдено или чужая.
    """
    stmt = (
        update(Reminder)
        .where(
            Reminder.id == reminder_id,
            Reminder.user_id == user_id,
            Reminder.status == "scheduled",
        )
        .values(status="cancelled")
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return CancelOutcome(kind="cancelled")

    status_stmt = select(Reminder.status).where(
        Reminder.id == reminder_id,
        Reminder.user_id == user_id,
    )
    status_result = await session.execute(status_stmt)
    status = status_result.scalar_one_or_none()

    if status == "sending":
        return CancelOutcome(kind="already_sending")

    return CancelOutcome(kind="not_found")


async def set_user_timezone(
    session: AsyncSession,
    telegram_user_id: int,
    timezone: str,
    now: datetime,
) -> bool:
    """Обновляет часовой пояс пользователя.

    Валидация IANA-зоны выполняется сценарием ``services`` до вызова; репозиторий
    только сохраняет значение. Одновременно обновляется ``updated_at_utc``.

    Args:
        session: открытая ``AsyncSession``.
        telegram_user_id: PRIMARY KEY пользователя.
        timezone: новое значение IANA-зоны.
        now: момент изменения (aware UTC).

    Returns:
        ``True``, если обновлён ровно один пользователь (``rowcount == 1``);
        иначе ``False`` (пользователь не найден).
    """
    stmt = (
        update(User)
        .where(User.telegram_user_id == telegram_user_id)
        .values(timezone=timezone, updated_at_utc=now.isoformat())
    )
    result = await session.execute(stmt)

    if result.rowcount == 1:
        await session.commit()
        return True

    return False
