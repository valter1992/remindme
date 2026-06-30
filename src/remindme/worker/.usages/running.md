# Worker уведомлений — запуск цикла доставки

Домен: запуск бесконечного цикла доставки просроченных напоминаний в одном event loop с long polling.
Аудитория: клетка `main` (точка входа).

Worker работает как фоновая задача рядом с polling. Каждую итерацию открывает свою сессию через `session_factory`, выбирает просроченные `scheduled`-напоминания, атомарно захватывает каждое (`scheduled → sending`), отправляет текст и отмечает отправку (`→ sent`).

## Запуск

```python
import asyncio
from .worker import run_reminder_worker

asyncio.create_task(
    run_reminder_worker(bot=bot, session_factory=session_factory, poll_interval=settings.REMINDER_POLL_INTERVAL_SECONDS)
)
await dp.start_polling(bot)
```

`poll_interval` берётся из настроек (`REMINDER_POLL_INTERVAL_SECONDS`, положительное целое). `session_factory` и `bot` строятся точкой входа один раз.

## Восстановление при старте, повторы и failed — этап 3

Worker при старте один раз возвращает зависшие записи `sending` (`locked_at_utc` старше 60 с) в `scheduled`, а
просроченные активные напоминания доставляются обычным циклом независимо от длительности простоя.

Внутри `run_reminder_worker`:
- на старте — один вызов `recover_stuck_sending(session, now, stale_after_seconds=60)`;
- на каждую итерацию — `find_due_reminders` → `claim_for_sending` → `send_message`;
- `TelegramForbiddenError` (бот заблокирован пользователем) → `mark_failed` немедленно, без повторов;
- иная ошибка `send_message` → `record_send_failure`: 1–3-я неудача назначают следующий `attempt` через 30/120/600 с,
  4-я → `failed`.

## Предусловия и побочные эффекты

- Запускается как asyncio-задача в том же event loop, что и polling; `session_factory` и `bot` уже построены.
- Атомарный захват (`claim_for_sending`, `UPDATE … WHERE status='scheduled'` + `rowcount`) гарантирует, что две итерации не заберут одну запись.
- Отменённые записи (`cancelled`) не отправляются — `claim_for_sending` захватывает только `scheduled`.
- Recovery выполняется ровно один раз при старте, до основного цикла; `next_attempt_at_utc` не пересчитывается —
  запись остаётся due и доставляется сразу.
- Блокировка бота — постоянная ошибка: запись сразу в `failed` (не проходит расписание повторов).
- Преходящие ошибки `send_message` ретраятся at-least-once: дубли при аварии между отправкой и фиксацией статуса
  допустимы (out of scope — exactly-once не требуется).
- Логи: WARNING с `user_id`, `attempt_count`, кодом ошибки и длительностью; токен и полный текст записи никогда не
  попадают в логи и тексты; `chat_id` = `reminder.user_id`.
