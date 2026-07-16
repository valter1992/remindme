"""ORM-модели клетки db.

Декларативные модели SQLAlchemy 2.x для четырёх сущностей хранилища RemindMe:
``User``, ``Reminder``, ``Note``, ``Todo``. Все timestamp-поля хранятся как
``Text`` в формате ISO 8601 (UTC), чтобы не зависеть от собственной поддержки
типов SQLite под драйвером ``aiosqlite``. Внешние ключи включены (PRAGMA
``foreign_keys=ON`` на каждом соединении), поэтому операции с несуществующим
``user_id`` завершаются ``IntegrityError``.

``Base`` — декларативный корень; его ``metadata`` — источник схемы для миграций
и ``create_all`` в тестах.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Декларативный корень ORM; держит ``metadata`` всех моделей.

    Используется миграциями и созданием схемы в тестах
    (``Base.metadata.create_all``).
    """


class User(Base):
    """Пользователь бота.

    ``telegram_user_id`` — PRIMARY KEY и UNIQUE (идемпотентность регистрации при
    конкурентных сообщениях); цель FK для ``Reminder``, ``Note``, ``Todo``.
    """

    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(primary_key=True, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at_utc: Mapped[str] = mapped_column(Text, nullable=False)


class Reminder(Base):
    """Одноразовое напоминание; ``user_id`` → FK на ``users.telegram_user_id``.

    Attributes:
        id: PRIMARY KEY autoincrement.
        user_id: FK на users.telegram_user_id, NOT NULL.
        text: Текст напоминания, NOT NULL.
        remind_at_utc: Момент напоминания, ISO 8601 UTC, NOT NULL.
        status: NOT NULL: scheduled|sending|sent|cancelled|failed.
        attempt_count: NOT NULL, default 0.
        next_attempt_at_utc: Время следующей попытки, ISO 8601 UTC, NOT NULL.
        locked_at_utc: Время захвата worker, nullable.
        created_at_utc: Время создания, ISO 8601 UTC, NOT NULL.
        sent_at_utc: Время отправки, nullable.
    """

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_user_id"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    locked_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)


class Note(Base):
    """Заметка без статуса и срока; ``user_id`` → FK на ``users.telegram_user_id``.

    Attributes:
        id: PRIMARY KEY autoincrement.
        user_id: FK на users.telegram_user_id, NOT NULL.
        text: Текст заметки, NOT NULL.
        created_at_utc: Время создания, ISO 8601 UTC, NOT NULL.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_user_id"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)


class Todo(Base):
    """Задача с необязательным сроком и статусом.

    ``user_id`` → FK на ``users.telegram_user_id``.

    Attributes:
        id: PRIMARY KEY autoincrement.
        user_id: FK на users.telegram_user_id, NOT NULL.
        text: Текст задачи, NOT NULL.
        due_at_utc: Срок задачи, nullable.
        status: NOT NULL: active|completed.
        created_at_utc: Время создания, ISO 8601 UTC, NOT NULL.
        completed_at_utc: Время завершения, nullable.
    """

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_user_id"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)
