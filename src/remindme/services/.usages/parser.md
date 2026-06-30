# Парсер времени напоминания — разбор текста в UTC-момент

Домен: детерминированный разбор времени напоминания из текста команды/фразы в aware UTC-момент.
Аудитория: клетка `bot` (обработчики) и тесты `tests/unit/test_parser.py`.

Парсер — чистая функция: не обращается к Telegram и БД, не вызывает `datetime.now()` — текущий момент передаётся аргументом `now`. Возвращает `ParsedReminder` (успех) или `ParseError` (типизированная ошибка).

## Режимы разбора

Режим определяется по содержимому `raw`:
- команда `/remind` — текст разделяется по первому `|`: слева время, справа текст напоминания;
- фраза «напомни [мне] …» — текст после префикса разбирается по 5 шаблонам (`re.IGNORECASE`, приоритет от частного к общему).

## Поддерживаемые формы времени

- абсолютная: `YYYY-MM-DD HH:MM`, `DD.MM HH:MM`;
- относительная: «через N минут/минуты/минуту/час/часа/дней/дня …» (все формы единиц);
- «сегодня [в HH:MM]», «завтра [в HH:MM]» — без времени используется `default_time`.

## Вызов

```python
from .services import parse_remind_time, ParsedReminder, ParseError

result = parse_remind_time(
    raw=message.text,
    now=fixed_now,          # aware UTC datetime
    timezone=user.timezone,
    default_time="09:00",
)
if isinstance(result, ParseError):
    # result.kind ∈ {invalid_format, date_not_exist, past, horizon_exceeded, empty_text, text_too_long, nonexistent_time, ambiguous_time}
    ...
else:  # ParsedReminder
    result.remind_at_utc    # aware UTC datetime
    result.text             # очищенный текст без временной части
```

## Предусловия и побочные эффекты

- `now` — aware UTC datetime, фиксируется один раз; реальные `sleep` в тестах не используются.
- Календарная валидация через `strptime`: `31.02` → `date_not_exist`.
- Горизонт > 365 дней → `horizon_exceeded`; момент в прошлом или ровно `now` → `past`.
- Пустой текст напоминания → `empty_text`.
- Длина текста напоминания > 500 символов → `text_too_long`.
- Неподдерживаемая свободная фраза («в пятницу вечером») → `invalid_format`.

## Перевод часов (DST) — этап 3

При разборе абсолютного/«сегодня-завтра» времени парсер локализует naive datetime в зоне пользователя. В дни перевода
часов локальное время может не существовать (весенний переход «вперёд») или быть неоднозначным (осенний «назад»):

```python
result = parse_remind_time(raw="25.03 03:30", now=now, timezone="Europe/Moscow", default_time="09:00")
if isinstance(result, ParseError):
    # result.kind == "nonexistent_time"  — такого локального времени нет в этот день
    # result.kind == "ambiguous_time"    — время двусмысленно (две интерпретации UTC)
    ...
```

- `nonexistent_time` — весенний переход: локальный момент выпадает (например 02:00→03:00).
- `ambiguous_time` — осенний переход: локальный момент встречается дважды (например 03:00→02:00).
- Оба случая пользователь должен указать другое время; обработчик показывает соответствующий текст через
  `format_parse_error`.