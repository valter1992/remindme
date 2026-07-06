"""Начальная ревизия: полная схема RemindMe (users, reminders, notes, todos).

Revision ID: 217646b92c67
Revises:
Create Date: 2026-07-06 14:08:04.706193

Создаёт четыре таблицы ``Base.metadata`` (``users``, ``reminders``, ``notes``,
``todos``) с первичными ключами, ``UNIQUE`` на ``users.telegram_user_id`` и
внешними ключами ``user_id → users.telegram_user_id``. Все timestamp-поля —
``Text`` (ISO 8601 UTC); ``Reminder.attempt_count`` без ``server_default``
(дефолт задаётся ORM). Ревизия совпадает с ``Base.metadata`` 1:1 (автогенерация
после ``upgrade`` не находит расхождений).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "217646b92c67"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт полную схему четырёх таблиц RemindMe."""
    op.create_table(
        "users",
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("updated_at_utc", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("telegram_user_id"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("remind_at_utc", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at_utc", sa.Text(), nullable=False),
        sa.Column("locked_at_utc", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("sent_at_utc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "todos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("due_at_utc", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.Text(), nullable=False),
        sa.Column("completed_at_utc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Удаляет схему в порядке, обратном созданию (сначала зависимые таблицы)."""
    op.drop_table("todos")
    op.drop_table("reminders")
    op.drop_table("notes")
    op.drop_table("users")
