"""Клетка main — точка входа приложения RemindMe.

Применяет миграции схемы до старта polling, собирает инфраструктуру данных
(engine/factory), инициализирует бота и диспетчер, регистрирует обработчики и
меню команд, запускает цикл доставки worker как фоновую задачу рядом с long
polling в одном event loop. Миграции применяются программно in-process через
``alembic`` (:func:`apply_migrations` → ``command.upgrade("head")``); токен и
тексты записей не логируются.

Принцип «явного времени» здесь не применяется: точка входа не оперирует
пользовательским ``now`` (миграции и сборка инфраструктуры не зависят от
текущего момента); ``datetime.now`` используется только внутри worker и
handler'ов.
"""

import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from alembic import command
from alembic.config import Config

from ..bot import register_handlers
from ..config import get_settings
from ..db import create_engine, create_session_factory
from ..worker import run_reminder_worker

__all__ = ["apply_migrations", "main", "set_commands"]

logger = logging.getLogger(__name__)

# Корень репозитория: здесь лежат ``alembic.ini`` и каталог миграций ``alembic/``.
# ``main.py`` → ``main/`` → ``remindme/`` → ``src/`` → корень.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_ALEMBIC_DIR = _REPO_ROOT / "alembic"

# Меню команд бота: этап 1 (/start,/help,/remind,/reminders), этап 3
# (/timezone,/cancel), этап 4 (/note,/notes,/todo,/todos,/completed).
_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="start", description="Начать работу с ботом"),
    BotCommand(command="help", description="Список команд и примеры"),
    BotCommand(command="remind", description="Создать напоминание"),
    BotCommand(command="reminders", description="Список напоминаний"),
    BotCommand(command="timezone", description="Установить часовой пояс"),
    BotCommand(command="cancel", description="Отменить напоминание по id"),
    BotCommand(command="note", description="Создать заметку"),
    BotCommand(command="notes", description="Список заметок"),
    BotCommand(command="todo", description="Создать задачу"),
    BotCommand(command="todos", description="Список активных задач"),
    BotCommand(command="completed", description="Список завершённых задач"),
)


async def set_commands(bot: Bot) -> None:
    """Регистрирует меню команд бота через Telegram API ``set_my_commands``.

    Args:
        bot: экземпляр aiogram ``Bot``.
    """
    await bot.set_my_commands(list(_COMMANDS))


def apply_migrations() -> None:
    """Применяет миграции схемы к настроенной БД программно in-process.

    Строит alembic ``Config`` по ``alembic.ini`` (путь вычисляется от модуля, а не
    от текущего каталога — устойчиво к ``cwd``), подставляет ``script_location`` и
    ``sqlalchemy.url`` из настроек (:func:`get_settings`) и выполняет
    ``command.upgrade("head")`` без вызова CLI и без порождения subprocess.
    Идемпотентен: повторный запуск — no-op, если схема уже актуальна.

    Raises:
        alembic.util.CommandError: при ошибке применения миграций.
    """
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)
    command.upgrade(config, "head")


async def main() -> None:
    """Точка входа приложения: миграции → сборка → регистрация → polling.

    Шаги строго по контракту: применить миграции (в отдельном потоке через
    :func:`asyncio.to_thread`, чтобы не вкладывать ``asyncio.run`` из
    ``alembic/env.py`` в текущий event loop и не блокировать его), получить
    настройки, построить engine и фабрику сессий, создать бота и диспетчер,
    зарегистрировать обработчики и меню команд, запустить worker как фоновую
    задачу, сбросить отложенные обновления и стартовать long polling в том же
    event loop. Worker и polling работают в одном процессе/loop.
    """
    await asyncio.to_thread(apply_migrations)

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    dp = Dispatcher()

    register_handlers(dp, session_factory)
    await set_commands(bot)

    worker_task = asyncio.create_task(
        run_reminder_worker(
            bot=bot,
            session_factory=session_factory,
            poll_interval=settings.REMINDER_POLL_INTERVAL_SECONDS,
        )
    )

    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        with contextlib.suppress(Exception):
            await bot.session.close()
        await engine.dispose()
