# Architecture Plan — RemindMe, Этап 5: Завершение

> Завершающий этап серии. Новая прикладная функциональность исключена — стабилизация, упаковка, докеризация,
> миграции, полное тестовое покрытие и документация запуска. Единственная изменяемая клетка — `main`
> (добавляется bootstrap миграций). Остальные клетки (`config`, `db`, `services`, `bot`, `worker`) не затронуты.

## Topic

`remindme-05-finishing` — план сохранён в `docs/arch/remindme-05-finishing.md`.

## Implementation Order

| # | Клетка | Действие | Обоснование порядка |
|---|---|---|---|
| 1 | `src/remindme/main` | MODIFY | Корневая клетка (зависит от `config`/`db`/`bot`/`worker`); модифицируется на месте. Новых клеток нет, поэтому leaves→root сводится к единственной корневой модификации. |

## Artifacts

### Cell: `src/remindme/main` (MODIFY)

#### Diff к существующему `src/remindme/main/CODEMANIFEST`

- **Usages (header):** ДОБАВИТЬ `alembic: .goga/usages/cooks/alembic.md` и `deployment: .goga/usages/deployment.md`.
- **Annotations (global):** ЗАМЕНИТЬ — добавить ссылки `alembic`/`deployment` и контекст «миграции до polling; запуск из venv или `docker compose up`».
- **Body:** ДОБАВИТЬ рутину `apply_migrations()`; МОДИФИЦИРОВАТЬ алгоритм `main()` (вставить шаг «Применить миграции: `apply_migrations()`» первым, перенумеровать остальные).
- **Footer.Description:** ОБНОВИТЬ — упомянуть применение миграций точкой входа.
- Imports — **без изменений** (`alembic` — внешняя библиотека через `Usages`; новых клеточных импортов нет).

#### Полный собранный `src/remindme/main/CODEMANIFEST`

```yaml
Imports:
  - Types:
      - get_settings
    Usages:
      - settings
    From: src/remindme/config
  - Types:
      - create_engine
      - create_session_factory
    Usages:
      - engine
    From: src/remindme/db
  - Types:
      - register_handlers
    Usages:
      - handlers
    From: src/remindme/bot
  - Types:
      - run_reminder_worker
    Usages:
      - running
    From: src/remindme/worker

Usages:
  conventions: .goga/usages/conventions.md
  aiogram: .goga/usages/cooks/aiogram.md
  alembic: .goga/usages/cooks/alembic.md
  deployment: .goga/usages/deployment.md

Annotations: |
  Используй `conventions` для написания правил и тестирования кода.
  Используй `aiogram` для Bot, Dispatcher и long polling.
  Используй `alembic` для применения миграций схемы при старте (программный `upgrade head` в том же процессе).
  Используй `deployment` для упаковки и запуска через Docker Compose (точка входа `main()` применяет миграции
  в процессе до старта бота — отдельный shell-entrypoint не нужен; `.env` прокидывается через `environment`/`env_file`,
  в образ не копируется).

  Точка входа приложения: один процесс, запуск из venv или через `docker compose up`. Перед long polling применяются
  миграции (`apply_migrations`), затем запускается worker как фоновая задача. Токен берётся из настроек и передаётся
  в Bot; не логируется. Worker и polling работают в одном event loop; recovery зависших sending выполняется внутри worker.

---

"set_commands(bot: Bot)":
  location: main.py
  annotations: |
    Регистрирует меню команд. Инкрементально: этап 1 (/start,/help,/remind,/reminders),
    этап 3 (/timezone,/cancel), этап 4 (/note,/notes,/todo,/todos,/completed).

    `bot`: экземпляр Bot

    Algorithm:
    1. Вызвать bot.set_my_commands со списком BotCommand для /start,/help,/remind,/reminders,
       /timezone,/cancel,/note,/notes,/todo,/todos,/completed

    Requirements:
    - Регистрируются только команды, реализованные на текущем этапе

    Use `aiogram` (set_my_commands, BotCommand).

"apply_migrations()":
  location: main.py
  annotations: |
    Применяет миграции схемы к настроенной БД перед стартом приложения (bootstrap полной схемы).

    Algorithm:
    1. Через `alembic` применить все миграции к head программно в текущем процессе (программный API `upgrade head`,
       без вызова CLI и без порождения subprocess)
    2. URL базы данных (DATABASE_URL) разрешается в env.py через `get_settings`; env имеет приоритет над .env

    Requirements:
    - выполняется до запуска polling и worker; создаёт полную схему (4 таблицы + индексы) одной начальной ревизией
    - идемпотентен: повторный запуск — no-op, если схема актуальна

    Constraints:
    - не принимает URL явно — его разрешает env.py
    - не порождает subprocess для миграций; не логирует токен и тексты записей
    - `command.upgrade` синхронен: в асинхронном `main` обёртывается через `asyncio.to_thread` (или эквивалент),
      чтобы не блокировать event loop; async-движок корректно применяется через `run_sync` в env.py

    Use `alembic` (programmatic `command.upgrade` in-process; async env via `run_sync`); `conventions` (structured logging).

"main()":
  location: main.py
  annotations: |
    Точка входа приложения: применение миграций, сборка инфраструктуры, инициализация бота, регистрация обработчиков,
    запуск worker и long polling.

    Algorithm:
    1. Применить миграции: `apply_migrations()` (полная схема до старта)
    2. Получить настройки через `get_settings`
    3. Построить async-движок и фабрику сессий (`create_engine`, `create_session_factory`)
    4. Создать бота с токеном из настроек
    5. Создать диспетчер
    6. Зарегистрировать обработчики через `register_handlers`, передав построенную фабрику сессий
    7. Регистрировать меню команд через `set_commands`
    8. Запустить цикл доставки как asyncio-задачу: `run_reminder_worker`(bot, session_factory, settings.REMINDER_POLL_INTERVAL_SECONDS)
    9. Сбросить отложенные обновления перед стартом
    10. Запустить long polling (worker работает параллельно в том же event loop)

    Requirements:
    - Приложение работает в одном процессе
    - миграции применяются до запуска polling (контракт «точка входа применяет миграции перед стартом бота»)

    Constraints:
    - Токен не логируется и не попадает в тексты ошибок
    - Worker и polling работают в одном event loop

    Use `aiogram` (Bot, Dispatcher, start_polling); `apply_migrations`; `settings`; `engine`; `handlers`; `running` (run_reminder_worker).

---

Author: Goga
CreatedAt: 29/06/26
Description: |
  Точка входа RemindMe: применение миграций, сборка инфраструктуры данных, инициализация бота, регистрация обработчиков,
  запуск worker уведомлений (с восстановлением зависших при старте) и long polling.
```

#### `.usages/` файлы

**Файл:** `src/remindme/main/.usages/running.md` (РАСШИРИТЬ — заменить содержимое):

```md
# Запуск RemindMe

Домен: операционный запуск бота RemindMe (точка входа `main`).
Аудитория: разработчик/оператор, запускающий бота локально или через Docker Compose.

Точка входа `main()` перед long polling применяет миграции схемы (`apply_migrations` → полная схема SQLite), затем
запускает цикл доставки worker как фоновую задачу и long polling в одном event loop. Токен берётся из настроек
(`TELEGRAM_BOT_TOKEN`) и не логируется.

## Предусловия
- Python 3.10+, активированный venv с зависимостями из `pyproject.toml`
- `TELEGRAM_BOT_TOKEN` от BotFather — в `.env` или переменной окружения
- `DATABASE_URL` (по умолчанию `sqlite+aiosqlite:///data/remindme.db`); директория `data/` создаётся автоматически

## Запуск локально (venv)
`python -m remindme.main`
При старте: миграции → регистрация команд → worker → long polling. Проверка: `/start` отвечает приветствием.

## Запуск через Docker Compose
`docker compose up -d` затем `docker compose logs -f`
Compose поднимает сервис бота: применяет миграции, стартует long polling, монтирует том `data/` для SQLite.
Проверка: отправить `/start`.

## Поведение при перезапуске
- База в `data/` (том в compose) — записи сохраняются между перезапусками контейнера.
- Просроченное за время простоя напоминание доставляется после старта: worker recovery возвращает зависшие `sending`,
  а `find_due_reminders` выбирает всё просроченное по `next_attempt_at_utc`.

## Побочные эффекты
- Создаётся/обновляется `data/remindme.db` и WAL-журнал.
- Меню команд бота регистрируется через `set_my_commands` при каждом старте.
```

**Файл:** `.goga/usages/deployment.md` (СОЗДАТЬ, project-level):

```md
# Deployment — упаковка и запуск RemindMe в Docker

Домен: упаковка приложения RemindMe в образ и запуск через Docker Compose.
Аудитория: сопровождение Dockerfile/docker-compose.yml и CI.

RemindMe — однопроцессное приложение. Образ содержит код и зависимости; секреты и база данных пробрасываются снаружи.

## Dockerfile
- Базовый образ Python 3.10+ slim; зависимости из `pyproject.toml` в venv.
- Копируются `src/`, `alembic/`, `alembic.ini`, `pyproject.toml`.
- Рабочая директория — корень проекта; точка входа `python -m remindme.main` (модуль `main` сам применяет миграции через `apply_migrations`).
- `data/` НЕ создаётся в образе — монтируется томом; `.env` НЕ копируется в образ (`COPY .env` запрещён).

## docker-compose.yml
- Один сервис бота; `build: .`; `restart: unless-stopped`.
- Том для `data/` (SQLite + WAL переживает перезапуск контейнера).
- `TELEGRAM_BOT_TOKEN` и настройки — через `env_file: .env` и/или `environment:`; никогда не вносятся в образ.
- Портов наружу не выставляется (long polling; исходящие соединения к Telegram API).

## Порядок запуска (миграции → бот)
Точка входа `main()` сначала вызывает `apply_migrations()` (`alembic upgrade head`), затем запускает worker и long polling.
Отдельный shell-entrypoint для миграций не требуется — миграции применяются в том же процессе.

## Безопасность
- `TELEGRAM_BOT_TOKEN` хранится как `SecretStr`; в логи и тексты ошибок не попадает.
- `.env` и `data/` в `.gitignore`; образ собирается без секретов и без данных.

## Проверка
- `docker compose up` → бот отвечает на `/start`.
- `docker compose restart` → записи сохранены, просроченное напоминание доставляется.
```

## Dependency Map

```
config ──────────────────────────────────────────────┐
                                                       │
db ────────────────────────────────────────────┐      │
   │                                            │      │
   ├──► services ──────────────────────┐        │      │
   │                                    │        │      │
   └────────────────────► bot ──────────┼────────┼──► worker ──┐
                                        │        │              │
                                        └────────┴──────────────┴──► main (MODIFY)
                                                                       └─► apply_migrations (intra-cell, NEW)
                                                                              └─ alembic (external, via Usages)
```

- Межклеточные Imports клетки `main` — **без изменений**: `config.get_settings`, `db.create_engine`/`create_session_factory`, `bot.register_handlers`, `worker.run_reminder_worker`.
- Новое ребро `main → apply_migrations` — внутриклеточное (не Imports).
- `alembic` — внешняя библиотека, подключена через `Usages` (не клетка).
- Циклов нет.

## Verification Checklist

### По артефактам клетки `main`
- [ ] `goga lint` и `goga schema` проходят для `src/remindme/main/CODEMANIFEST` (валидный DSL: Header→`---`→Body→`---`→Footer; casing ключей; `apply_migrations`/`main` — Routine без members; `location: main.py`).
- [ ] В `Usages` присутствуют `conventions`, `aiogram`, `alembic`, `deployment` — и каждая практика упомянута хотя бы в одной аннотации.
- [ ] В алгоритме `main()` первым шагом идёт вызов `apply_migrations()`.
- [ ] `src/remindme/main/.usages/running.md` самодостаточен, описывает локальный запуск и `docker compose up`.
- [ ] `.goga/usages/deployment.md` создан, описывает Dockerfile/compose/том/секреты/запрет `COPY .env`.

### Сопутствующие project-артефакты (вне клеток; governed usages)
- [ ] `pyproject.toml` создан (`conventions` + `pytest-asyncio`: `asyncio_mode=auto`, конфиг `ruff`, зависимости, `[project.optional-dependencies].test`).
- [ ] `alembic/` (`cooks/alembic.md`): `alembic init -t async`, `env.py` берёт `DATABASE_URL` из `get_settings`, `PRAGMA foreign_keys=ON`, единая начальная ревизия (4 таблицы + индексы); `alembic upgrade head` создаёт схему, `downgrade -1` откатывает; `cooks/alembic.md` содержит секцию программного in-process запуска (`command.upgrade` без subprocess, async-движок через `run_sync`).
- [ ] `Dockerfile` + `docker-compose.yml` (`deployment.md`): без `COPY .env`, том `data/`, проброс `TELEGRAM_BOT_TOKEN` через `env_file`/`environment`.
- [ ] `tests/` (`conventions` Testing + `pytest-asyncio`): зеркало `src/` — `unit/` (parser/time/formatter/scenarios), `integration/` (репозитории/миграции/worker), handlers (mock Telegram), e2e smoke через `pytest.mark.skipif`.
- [ ] `.gitignore` дополнен `data/`, `*.db`.
- [ ] Финальная чистка: `pytest tests/ -x`, `ruff check src/`, `ruff format --check` — зелёные; в логах/ошибках нет токена и текстов записей.
- [ ] Все 11 критериев приёмки ТЗ проверены по чек-листу.
