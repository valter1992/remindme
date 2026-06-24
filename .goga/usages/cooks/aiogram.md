# aiogram 3.x — Telegram Bot API

Домен: интеграция с Telegram Bot API в боте RemindMe.
Аудитория: разработчики модулей `src/bot/` — handlers, callbacks, keyboards.

Бот работает через long polling в одном процессе. Webhook, домен и публичный HTTP-сервер не нужны. Бот обрабатывает сообщения только в личных чатах; в группах отвечает, что режим не поддерживается, и не сохраняет данные.

## Инициализация бота и диспетчера

```python
from aiogram import Bot, Dispatcher

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
```

`token` берётся из конфигурации и не должен попадать в логи и тексты ошибок.

## Long polling

Цикл опроса запускается в `main.py` вместе с фоновым worker уведомлений.

```python
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)
```

## Регистрация меню команд

При запуске регистрируется список команд, видимый пользователю в интерфейсе Telegram.

```python
from aiogram.types import BotCommand

async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="remind", description="Создать напоминание"),
        BotCommand(command="note", description="Создать заметку"),
        BotCommand(command="todo", description="Создать задачу"),
        BotCommand(command="reminders", description="Мои напоминания"),
        BotCommand(command="notes", description="Мои заметки"),
        BotCommand(command="todos", description="Мои задачи"),
        BotCommand(command="completed", description="Выполненные задачи"),
        BotCommand(command="timezone", description="Изменить часовой пояс"),
    ])
```

## Командные и текстовые обработчики

Команды разбираются `Command`-фильтром; свободный текст (например, фраза `напомни ...`) ловится отдельным message-обработчиком без фильтра команды.

```python
from aiogram.filters import Command
from aiogram.types import Message

@dp.message(Command("start"))
async def cmd_start(message: Message):
    ...

@dp.message()
async def handle_text(message: Message):
    ...
```

Порядок регистрации handlers важен: командные обработчики регистрируются раньше общего текстового, чтобы команда не уходила в общий разбор.

## Только личные чаты

Все обработчики фильтруются по типу чата. Несоответствие типа не должно приводить к сохранению данных.

```python
from aiogram.filters import BaseFilter

class PrivateOnly(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type == "private"
```

## Callback-запросы от inline-кнопок

Inline-кнопки используют формат `callback_data` вида `<action>:<entity>:<id>` (`complete:todo:42`, `delete:reminder:15`). Обработчик получает callback, проверяет владельца записи и отвечает.

```python
from aiogram.types import CallbackQuery

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    action, entity, raw_id = callback.data.split(":")
    await callback.answer()  # подтверждение нажатия
    ...
```

## Отправка и редактирование сообщений

- `sendMessage` — ответы и уведомления;
- `editMessageText` — обновление списка после inline-действия;
- `answerCallbackQuery` — подтверждение нажатия кнопки и текст для устаревших кнопок (`Запись уже изменена или удалена.`).

## Отправка уведомления пользователю

Так как бот работает только в личных чатах, `telegram_user_id` используется как `chat_id`.

```python
await bot.send_message(chat_id=user_id, text=text)
```

## Обработка ошибок Telegram

Ошибки отправки перехватываются на уровне worker: увеличивается счётчик попыток и назначается следующая попытка по расписанию 30, 120 и 600 секунд. Блокировка бота пользователем после четырёх неудач приводит запись в статус `failed`.
