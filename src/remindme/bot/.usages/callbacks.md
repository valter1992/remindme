# Callbacks — inline-управление заметками, задачами, напоминаниями

Домен: единый callback-роутер inline-кнопок `<action>:<entity>:<id>` с проверкой владельца.
Аудитория: клетка `main` (регистрация через `register_handlers`) и будущие расширения inline-управления.

Inline-кнопки крепятся к сообщению-списку целиком (ряд на запись). Формат callback_data: `complete:todo:<id>`, `delete:note:<id>`, `delete:todo:<id>`, `delete:reminder:<id>`.

```python
from aiogram.types import CallbackQuery

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    action, entity, raw_id = callback.data.split(":")
    record_id = int(raw_id)
    user_id = callback.from_user.id
    # действие через репозиторий/сценарий с фильтром (record_id, user_id)
    # устаревшая/чужая кнопка → callback.answer("Запись уже изменена или удалена.")
    # успех → callback.message.edit_text(...обновлённый список..., reply_markup=kb)
    await callback.answer()
```

## Перерисовка списка после действия

Целевой список для `editMessageText` определяется по статусу записи (не хранится в callback_data):
- `complete:todo`, `delete:todo` (was active) → `/todos`; `delete:todo` (was completed) → `/completed`.
- `delete:note` → `/notes`; `delete:reminder` → `/reminders`.
- `delete:reminder` при `already_sending` → текст этапа 3; при `not_found` → «Запись уже изменена или удалена.»

## Предусловия и побочные эффекты

- Проверка владельца — на стороне репозитория/сценария по `(record_id, user_id)`; callback не доверяет `id` без `user_id`.
- `now` (aware UTC) фиксируется один раз — для `complete_todo` (completed_at_utc).
- Чужие записи не раскрываются (единый ответ «Запись уже изменена или удалена.»).
