# RemindMe — Этап 2: Главный сценарий (напоминания)

## Current State

После этапа 1 есть каркас: конфигурация, модели (включая `Reminder`), движок/сессии SQLite, единая миграция полной схемы, бот на long polling с `/start`,`/help` и авто-регистрацией пользователя, тестовые фикстуры. Нет разбора команд, создания/просмотра/удаления напоминаний и доставки уведомлений.

## Description

Реализовать основной сценарий: детерминированный парсер времени, создание напоминаний по `/remind` и фразе «напомни …», просмотр напоминаний и сервис отмены (`scheduled`→`cancelled`; UI кнопки «Удалить» — этап 4), базовый цикл доставки worker и форматирование ответов.

## Scope

**In scope:**
- `src/services/parser.py` — `parse_remind_time(raw, now, timezone, default_time)`: чистая функция без зависимостей от Telegram/БД. Режимы: `/remind` (split по первому `|`) и фраза `напомни [мне]` (5 шаблонов, приоритет от частного к общему, `re.IGNORECASE`). Возвращает типизированный результат (UTC-момент + очищенный текст) либо типизированную ошибку (неверный формат / дата не существует / прошлое / горизонт > 365 дней / пустой текст). Календарная проверка через `strptime`. `now` фиксируется один раз.
- `src/services/reminders.py` — сценарий создания (парсер → проверка границ → сохранение со `status='scheduled'`,`attempt_count=0`,`next_attempt_at_utc=remind_at_utc` → подтверждение с локальным временем) и сценарий отмены (`scheduled`→`cancelled`).
- `src/db/repositories.py` — `create_reminder`, `list_reminders` (только `scheduled`, `order_by remind_at_utc`, `limit 20`), `cancel_reminder` (фильтр по `id`+`user_id`+`status='scheduled'`, возврат `rowcount`). Откат транзакции и логирование на уровне репозитория.
- `src/worker/notifications.py` — базовый цикл: каждые `REMINDER_POLL_INTERVAL_SECONDS` искать `scheduled` с `next_attempt_at_utc <= now`, атомарный перевод в `sending` (`UPDATE … WHERE status='scheduled'`, проверка `rowcount`), `send_message`, успешный путь `→ sent` (`sent_at_utc`). Интеграция в `main.py` (polling + worker в одном event loop).
- `src/bot/handlers.py` — `/remind`, текстовый обработчик фразы «напомни …», `/reminders`, обработка ошибок формата/даты/прошлого/горизонта (шаблон + пример), лимиты длины сообщения 1000 и текста 500, ответ на неподдерживаемую свободную фразу и на обычный текст без команды.
- `src/bot/formatter.py` — подтверждение создания, текст уведомления (`Напоминание\n<текст>`), список напоминаний; единая helper-функция «aware UTC → локальная строка пользователя».
- `setMyCommands` добавляет `/remind`,`/reminders`.

**Out of scope:**
- Повторные попытки (30/120/600), `failed`, восстановление зависших `sending` при старте, немедленная отправка просроченных при простое (этап 3).
- Централизованный обработчик ошибок с текстом «Не удалось выполнить действие» (этап 3). Сырой `SQLAlchemyError` до пользователя в этапе 2 не доходит: репозиторий откатывает и логирует; пользовательский текст добавляется в этапе 3.
- `/timezone` и специальные DST-проверки неоднозначного времени (этап 3). Базовое DST через `zoneinfo` работает автоматически.
- Inline-кнопка «Удалить» и `editMessageText` (этап 4). Отмена доступна через сервис, но без UI-кнопки.

## Acceptance Criteria

- **Абсолютное время:** `/remind 2026-06-24 18:00 | Купить продукты` при `Europe/Moscow` и now 2026-06-23 12:00 создаёт напоминание на 24.06 18:00 MSK (UTC 15:00) с подтверждением в локальном времени.
- **Относительное время:** `напомни мне через 15 минут проверить духовку` при now 2026-06-24 12:00:00 UTC создаёт напоминание на 12:15:00 UTC с текстом `проверить духовку`.
- **Завтра без времени:** `напомни завтра купить молоко` при локальном now 2026-06-24 20:00 MSK и `DEFAULT_REMINDER_TIME=09:00` создаёт напоминание на 25.06 09:00 MSK.
- **Неверный формат:** `/remind Купить билеты` → запись не создаётся, бот показывает формат и пример.
- **Неподдерживаемая свободная фраза:** `напомни в пятницу вечером …` → отказ и допустимые шаблоны.
- **Напоминание в прошлом:** `сегодня в 19:00` при now 20:00 → отказ, просьба указать будущее время.
- **Доставка (базовый путь):** worker находит просроченное `scheduled`, отправляет текст, запись `→ sent`.
- **Отмена (сервис):** `cancel_reminder` переводит `scheduled`→`cancelled` (фильтр по `id`+`user_id`+`status='scheduled'`); worker не отправляет отменённую запись.
- **Изоляция списков:** `/reminders` содержит только записи текущего пользователя.
- Парсер покрыт параметризованными unit-тестами (все формы единиц, `мне`/без, регистр, `|`/без, `00:00`/`23:59`, `31.02`, прошлое, ровно `now`, граница 365 дней, 1/500/501 символ).

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
| pydantic-settings | `.goga/usages/cooks/pydantic-settings.md` | created |
| aiosqlite | `.goga/usages/cooks/aiosqlite.md` | created |
| pytest-asyncio | `.goga/usages/cooks/pytest-asyncio.md` | created |

## Risks and Constraints

- Атомарный захват записи (`UPDATE … WHERE status='scheduled'` по `rowcount`) обязателен уже в этапе 2, иначе две итерации цикла заберут одну запись; интеграционная проверка корректности проводится в этапе 3/5.
- Откат транзакции в репозитории (этап 2), пользовательский текст ошибки — централизованный handler в этапе 3.
- Парсер — самая плотная по тестам часть; DST-наборы добавляются в этапе 3.

## Scope Estimate

Single task. Вторая подзадача серии.

## Existing Architecture

Зависит от этапа 1: `Settings`, модели `User`/`Reminder`, engine/session, миграция, `PrivateOnly`, skeleton `main.py`, фикстуры `session`/`fixed_now`. Наполняет `services/{parser,reminders}.py`, `db/repositories.py`, `worker/notifications.py`, `bot/{handlers,formatter}.py`.

## Notes

- Базовая доставка живёт здесь, чтобы демонстрационный срез 1–3 и приёмочный критерий «Доставка уведомления» работали.
- Повторные попытки и восстановление — этап 3.
