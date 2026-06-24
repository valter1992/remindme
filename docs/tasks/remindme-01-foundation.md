# RemindMe — Этап 1: Основа

## Current State

Зелёное поле. Проект содержит только конфигурацию goga (`.goga/config.yml`, `.goga/usages/conventions.md` и созданные `cooks/*.md`) и техническое задание `docs/propose/project.md`. Нет исходного кода, `pyproject.toml`, виртуального окружения, подключения к Telegram, моделей и схемы БД.

## Description

Создать фундамент приложения: конфигурацию из `.env`, структуру пакетов `src/`, асинхронное подключение к SQLite, модели и единую начальную Alembic-миграцию полной схемы, бота на long polling с командами `/start` и `/help`, авто-регистрацию пользователя.

## Scope

**In scope:**
- `pyproject.toml` со всеми зависимостями и `[project.optional-dependencies].test`; конфигурация `ruff`, `pytest` (`asyncio_mode = "auto"`); виртуальное окружение.
- `src/config.py` — `Settings(BaseSettings)`: `SecretStr` для токена, валидатор `DEFAULT_TIMEZONE` через `available_timezones()`/`ZoneInfo`, валидатор `DEFAULT_REMINDER_TIME` (`ЧЧ:ММ`), ограничение `REMINDER_POLL_INTERVAL_SECONDS > 0`.
- `src/db/models.py` — `Base` и все 4 класса: `User`, `Reminder`, `Note`, `Todo` (полный маппинг, чтобы `Base.metadata` был целым и миграция создавала все таблицы). Timestamp-поля — `Text` (ISO 8601).
- `src/db/session.py` — `create_async_engine`, `async_sessionmaker`, listener `PRAGMA foreign_keys=ON` + `journal_mode=WAL`, создание директории `data/`.
- `alembic/` — `alembic init -t async`, `env.py` с переопределением URL из настроек и `PRAGMA foreign_keys=ON`, единая начальная ревизия: 4 таблицы + 4 индекса + FK + `downgrade` в обратном порядке.
- `src/bot/handlers.py` — `/start` (идемпотентное создание `User` с `DEFAULT_TIMEZONE`, краткая инструкция), `/help`, обработчик неизвестной команды, фильтр `PrivateOnly`, авто-регистрация пользователя при первом сообщении (создаёт запись; до этапа 2 отвечает «используйте /help»).
- `src/main.py` — запуск polling + инкрементальное `setMyCommands` (на этапе 1 только `/start`, `/help`).
- `tests/conftest.py` — общие фикстуры (`session` через `tmp_path`, `fixed_now`, mock `bot`).

**Out of scope:**
- Парсер времени, `/remind`, фразы «напомни …», worker уведомлений (этап 2).
- Повторные попытки, восстановление после перезапуска, `/timezone`, DST (этап 3).
- Заметки, задачи, inline-кнопки (этап 4).
- Docker Compose, end-to-end smoke, полная документация запуска (этап 5).

## Acceptance Criteria

- Приложение стартует из venv с `.env` и отвечает на `/start`; создаётся запись в `users` с `timezone = DEFAULT_TIMEZONE`; повторный `/start` не дублирует запись и обновляет `updated_at_utc`.
- Первое входящее сообщение без `/start` создаёт запись пользователя с `timezone = DEFAULT_TIMEZONE` (авто-регистрация); `/help` показывает список доступных команд.
- `alembic upgrade head` создаёт все 4 таблицы, индексы `idx_reminders_delivery`, `idx_reminders_user_time`, `idx_notes_user_created`, `idx_todos_user_status_due` и FK; `alembic downgrade -1` откатывает без ошибок порядка FK.
- На соединении активны `PRAGMA foreign_keys=ON` и `journal_mode=WAL` (интеграционный тест: вставка reminder с несуществующим `user_id` → `IntegrityError`).
- Запуск без `TELEGRAM_BOT_TOKEN`, с неизвестной зоной или с `DEFAULT_REMINDER_TIME=9` завершается понятной ошибкой, не содержащей значения токена.
- В групповом чате бот отвечает, что режим не поддерживается, и не пишет в БД.
- `ruff check src/` и `pytest tests/ -x` проходят (тесты: валидаторы настроек, идемпотентный `/start`, применение/откат миграции).

## Stack

- **Frameworks:** aiogram 3.x
- **Libraries:** pydantic 2.x, pydantic-settings, SQLAlchemy 2.x (async), aiosqlite, Alembic
- **Infrastructure:** SQLite (WAL, `PRAGMA foreign_keys=ON`)
- **Инструменты:** ruff, pytest, pytest-asyncio, pytest-cov

## External Dependencies

| Component | Usage file | Status |
|---|---|---|
| aiogram | `.goga/usages/cooks/aiogram.md` | created |
| SQLAlchemy | `.goga/usages/cooks/sqlalchemy.md` | created |
| Alembic | `.goga/usages/cooks/alembic.md` | created |
| pydantic-settings | `.goga/usages/cooks/pydantic-settings.md` | created |
| aiosqlite | `.goga/usages/cooks/aiosqlite.md` | created |
| pytest-asyncio | `.goga/usages/cooks/pytest-asyncio.md` | created |

## Risks and Constraints

- Совместимость: `conventions.md` требует Python 3.10+; ТЗ и Docker-образ фиксируют 3.12 — целевая версия 3.12.
- Относительные импорты внутри пакетов, pydantic-модели с `kw_only=True`, docstrings в Google-стиле (по `conventions.md`).
- `PRAGMA foreign_keys` по умолчанию выключен в SQLite — включается на каждом соединении.
- Модели `Note`/`Todo` объявляются в этапе 1, но не используются до этапа 4 («мёртвый» код до того момента); этап 1 проверяет применение миграции (таблицы существуют), а не ORM-операции с ними.
- Идемпотентность регистрации при одновременных сообщениях — `UNIQUE` по `telegram_user_id`.

## Scope Estimate

Single task. Это первая подзадача из серии из пяти.

## Existing Architecture

Greenfield. Появляется каркас: `src/config.py`, `src/db/{models,session}.py`, `alembic/`, `src/bot/handlers.py` (`/start`, `/help`, авто-регистрация, обработка неизвестной команды), `src/main.py`. Модули `services`, `worker`, `bot/{callbacks,keyboards,formatter}` наполняются на этапах 2–4.

## Notes

- Порядок реализации серии: 1 Основа → 2 Напоминания → 3 Надёжность → 4 Заметки/задачи → 5 Завершение.
- Демонстрационный вертикальный срез для воркшопа — этапы 1–3.
- Меню команд (`setMyCommands`) формируется инкрементально: на каждом этапе регистрируются только реализованные команды.
- `/timezone` отнесён к этапу 3; на этапе 1 зона хранится в модели и используется при создании напоминаний.
