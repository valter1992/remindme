# RemindMe — Этап 4: Дополнительные сущности (заметки и задачи)

## Current State

После этапа 3 напоминания полностью реализованы с надёжной доставкой. Модели `Note`/`Todo` и их таблицы созданы миграцией этапа 1, но не используются. Нет команд `/note`,`/todo`, списков `/notes`,`/todos`,`/completed` и inline-управления.

## Description

Добавить заметки и задачи с командами создания и просмотра, inline-кнопками управления и проверкой владельца по каждой операции.

## Scope

**In scope:**
- `src/services/notes.py` — создание `/note` (текст 1–500), валидация.
- `src/services/todos.py` — создание `/todo` (срок `ГГГГ-ММ-ДД ЧЧ:ММ | текст` vs задача без срока; срок может быть в прошлом → просроченная активная), смена статуса `active`→`completed` с `completed_at_utc`.
- `src/db/repositories.py` — `create_note`,`list_notes` (`order_by created_at_utc desc`, `limit 20`),`delete_note` (физическое удаление, `id`+`user_id`); `create_todo`,`list_todos` (активные: сначала со сроком, затем без, `limit 20`),`list_completed` (`limit 20`),`complete_todo` (`id`+`user_id`+`status='active'`→`completed`),`delete_todo`.
- `src/bot/handlers.py` — `/note`,`/notes`,`/todo`,`/todos`,`/completed`.
- `src/bot/callbacks.py` — маршрутизация `callback_data` `<action>:<entity>:<id>`: `complete:todo`, `delete:reminder`/`delete:note`/`delete:todo`. Проверка владельца по `(id, user_id)` для каждого; устаревшая кнопка → «Запись уже изменена или удалена.»; `answerCallbackQuery`.
- `src/bot/keyboards.py` — inline-клавиатуры: напоминания — `Удалить`; заметки — `Удалить`; активные задачи — `Выполнено`,`Удалить`; выполненные — `Удалить`.
- `src/bot/formatter.py` — форматы списков заметок/задач/выполненных; `editMessageText` для обновления списка после действия.
- Кнопка удаления напоминания: `scheduled`→`cancelled` (worker не отправит); для `sending` — текст из этапа 3.
- `setMyCommands` добавляет `/note`,`/notes`,`/todo`,`/todos`,`/completed`.

**Out of scope:**
- Пагинация > 20, поиск, редактирование существующих записей (в «Что не входит в MVP»).
- Повторяющиеся напоминания, snooze-кнопки.
- Docker Compose, end-to-end smoke (этап 5).

## Acceptance Criteria

- **Управление задачей:** кнопка `Выполнено` → `completed`, `completed_at_utc` сохранён, задача уходит из `/todos` и появляется в `/completed`.
- **Изоляция данных:** callback с `id` записи пользователя B от имени пользователя A → данные не меняются и не раскрываются.
- **Изоляция списков:** `/notes`,`/todos`,`/completed` содержат только записи текущего пользователя.
- `/note` с пустым текстом или > 500 символов → отказ; `/note Код 1234` → создание и подтверждение.
- `/todo 2026-06-25 18:00 | Отчёт` создаёт задачу со сроком; `/todo Купить хлеб` — без срока; `/todo 2020-01-01 10:00 | Старое` — просроченная активная (уведомление не отправляется).
- Повторный клик по уже выполненной/удалённой задаче → «Запись уже изменена или удалена.»
- После inline-действия сообщение со списком обновляется (`editMessageText`).
- Удаление напоминания кнопкой `Удалить` переводит в `cancelled`; попытка удалить `sending` → текст этапа 3.

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
| pytest-asyncio | `.goga/usages/cooks/pytest-asyncio.md` | created |

## Risks and Constraints

- Inline-слой вводит проверку владельца на новом пути (callback); требуется единый паттерн safe-by-owner `(id, user_id)` уже отлаженный в репозиториях этапа 2.
- Переиспользование helper-функции локального времени из `formatter` (этап 2), чтобы не дублировать логику отображения.

## Scope Estimate

Single task. Четвёртая подзадача серии.

## Existing Architecture

Зависит от моделей/таблиц `Note`/`Todo` (миграция этапа 1), паттерна safe-by-owner репозиториев (этап 2), error-handling (этап 3). Наполняет `services/{notes,todos}.py`, `bot/{callbacks,keyboards}.py`, расширяет `db/repositories.py`,`bot/{handlers,formatter}.py`.

## Notes

- Функциональность отмены напоминания через сервис существует с этапа 2; здесь появляется её UI-кнопка.
