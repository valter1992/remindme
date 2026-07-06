# CLAUDE.md

Контрактный Telegram-бот RemindMe. Исходный код — `src/remindme/` (src-layout),
тесты зеркально — `tests/<cell>/test_<module>.py`. Контракты клеток —
`src/remindme/<cell>/CODEMANIFEST` (read-only; источник истины для API).
Авторитетные практики — `.goga/usages/` и `.goga/usages/cooks/`.

## Архитектура клеток

Шесть клеток, каждая — пакет `src/remindme/<cell>/` с фасадом `__init__.py`
(`__all__` экспортирует только собственные сущности; реэкспортов нет). Граф
импортов ацикличный и проверяется `goga schema`:

`config → db → services → bot`; `worker → bot + db`; `main → config + db + bot + worker`.

Порядок сборки снизу вверх: `config → db → services → bot → worker → main`.

## Принцип «явного времени»

`now` (aware UTC) — всегда параметр. `datetime.now()` допустимо **только** в
обработчиках бота (одно на сообщение) и в цикле worker. В парсерах, сценариях,
репозиториях и `ensure_user` — никогда. Это делает чистые функции
детерминированными и тестируемыми с фиксированным `now`.

## Инварианты БД (`src/remindme/db`)

- Все timestamp-поля — `Text` ISO 8601 UTC. Сравнение
  `next_attempt_at_utc <= now_iso` корректно лексикографически при фиксированном
  формате.
- Изоляция по владельцу: любой доступ к пользовательской сущности — через
  `(id, user_id)`. Поиск только по `id` запрещён. Исключение — delivery-примитивы
  worker (`mark_sent`/`mark_failed`/`record_send_failure`): запись уже захвачена
  worker и принадлежит захватившему процессу.
- Расписание повторов `{1: 30, 2: 120, 3: 600}` секунд; `failed` на 4-й неудаче
  (`mark_failed` НЕ инкрементирует `attempt_count`). `recover_stuck_sending`
  возвращает зависшие `sending` (старше 60 c) в `scheduled` при старте worker.
- При `SQLAlchemyError` репозиторий откатывает транзакцию, логирует контекст (без
  текста записи и токена) и повторно поднимает исключение.

## Токены и секреты

`TELEGRAM_BOT_TOKEN` — `SecretStr`; `.get_secret_value()` — только при
конструкции `Bot`. `handle_db_error` логирует `user_id` и имя класса исключения,
но никогда — текст записи или токен.

## Миграции

Применяются in-process через `apply_migrations()` (`alembic upgrade head`) до
polling, идемпотентны. Отдельного CLI/entrypoint нет. Корень с `alembic.ini` и
`alembic/` ищется модуль-относительно (checkout/editable) или относительно `cwd`
(Docker `WORKDIR`) — см. `main.py:_resolve_repo_root`.

## Толерантный парсер задач

В `parse_todo_input` разделитель `|` выделяет срок **только** когда слева
валидная дата `ГГГГ-ММ-ДД ЧЧ:ММ`; иначе весь текст (включая литерал `|`) — текст
задачи без срока. Это сделано намеренно по контракту, не баг. Past-срок
допускается; DST и горизонт НЕ проверяются.

## Сборка и проверка

```bash
pytest tests/ -x
ruff check src/ tests/
ruff format --check src/ tests/
goga lint       # cells: 6 errors: 0
goga schema     # ацикличный граф импортов
```

Локальный каталог `alembic/` затеняет установленный пакет `alembic`, поэтому в
`[tool.ruff.lint.isort]` стоит `known-third-party = ["alembic"]` — не удалять.

## Деплой

`Dockerfile` (`python:3.12-slim`, `pip install .`, `CMD ["python", "-m",
"remindme.main"]`) + `docker-compose.yml` (один сервис, том `./data:/app/data`,
`env_file: .env`, без портов). Секреты и `data/` не попадают в образ.
