# Alembic — миграции схемы SQLite

Домен: версионирование схемы базы данных RemindMe.
Аудитория: сопровождение схемы в `alembic/` при изменении моделей.

Схема создаётся миграциями Alembic поверх асинхронного движка SQLAlchemy. В проекте RemindMe полная схема (все четыре таблицы) создаётся одной начальной миграцией, поскольку она целиком описана в техническом задании.

## Инициализация для асинхронного движка

```bash
alembic init -t async alembic
```

Шаблон `async` генерирует `env.py`, рассчитанный на `create_async_engine` и `run_sync` для применения DDL.

## Привязка к URL базы данных

`alembic.ini` не хранит секреты и базовый URL жёстко; `sqlalchemy.url` переопределяется в `env.py` из конфигурации приложения.

```python
# alembic/env.py
from remindme.config import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)
```

## Контекст с внешними ключами

DDL SQLite выполняется через синхронный адаптер в `run_migrations_online`. Внешние ключи включаются PRAGMA перед применением операций.

```python
with connectable.connect() as connection:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
```

## Начальная миграция: полная схема

Единая ревизия создаёт таблицы `users`, `reminders`, `notes`, `todos`, их индексы и внешние ключи.

```python
def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_user_id", sa.Integer, primary_key=True),
        sa.Column("timezone", sa.Text, nullable=False),
        sa.Column("created_at_utc", sa.Text, nullable=False),
        sa.Column("updated_at_utc", sa.Text, nullable=False),
    )

    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.telegram_user_id"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("remind_at_utc", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at_utc", sa.Text, nullable=False),
        sa.Column("locked_at_utc", sa.Text, nullable=True),
        sa.Column("created_at_utc", sa.Text, nullable=False),
        sa.Column("sent_at_utc", sa.Text, nullable=True),
    )

    op.create_index(
        "idx_reminders_delivery", "reminders",
        ["status", "next_attempt_at_utc"],
    )
    op.create_index(
        "idx_reminders_user_time", "reminders",
        ["user_id", "remind_at_utc"],
    )
```

Индексы `idx_notes_user_created` и `idx_todos_user_status_due` создаются аналогично для `notes` и `todos`.

## Направление downgrade

`downgrade` удаляет объекты в порядке, обратном созданию: сначала индексы, затем таблицы, чтобы не нарушать внешние ключи.

## Запуск миграций

```bash
alembic upgrade head
alembic downgrade -1
alembic current
```

Миграции применяются к существующей базе без пересоздания данных; путь к файлу SQLite берётся из `DATABASE_URL`.
