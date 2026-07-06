"""Детерминированный парсер времени напоминания.

Чистая функция :func:`parse_remind_time` разбирает текст команды ``/remind``
или свободной фразы «напомни [мне] …» в aware-UTC момент напоминания с
DST-валидацией. Текущий момент фиксируется снаружи (параметр ``now``);
``datetime.now()`` внутри модуля не вызывается — функция детерминирована и
не обращается к Telegram/БД.

Поддерживаемые формы времени:
- относительная — «через N минут/часов/дней» (все формы единиц);
- «сегодня [в HH:MM]», «завтра [в HH:MM]» — без времени используется
  ``default_time``;
- абсолютная ISO — «YYYY-MM-DD HH:MM»;
- абсолютная «DD.MM[.YYYY] [в] HH:MM».

Результат — :class:`ParsedReminder` (успех) или :class:`ParseError`
(типизированная ошибка).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

__all__ = ["ParseError", "ParsedReminder", "parse_remind_time"]


class ParsedReminder(BaseModel):
    """Результат успеха парсера.

    Attributes:
        remind_at_utc: aware-UTC момент напоминания.
        text: очищенный текст напоминания без временной части.
    """

    model_config = ConfigDict(kw_only=True)

    remind_at_utc: datetime
    text: str


class ParseError(BaseModel):
    """Типизированная ошибка парсера.

    Attributes:
        kind: вариант ошибки — ``invalid_format`` / ``date_not_exist`` /
            ``past`` / ``horizon_exceeded`` / ``empty_text`` / ``text_too_long``
            / ``nonexistent_time`` / ``ambiguous_time``.
    """

    model_config = ConfigDict(kw_only=True)

    kind: str


# --- Регулярные шаблоны форм времени (применяются от частного к общему) ---

# «через N {unit}» — все формы единиц; группу единиц добираем по словарю.
_RELATIVE_RE = re.compile(
    r"^через\s+(?P<n>\d+)\s+"
    r"(?P<unit>минуту|минуты|минут|час|часа|часов|день|дня|дней)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# «сегодня [в HH:MM]» / «завтра [в HH:MM]» — время опционально.
_DAY_RE = re.compile(
    r"^(?P<word>сегодня|завтра)\b"
    r"(?:\s+(?:в\s+)?(?P<h>\d{1,2}):(?P<m>\d{2}))?\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Префикс фразы «напомни [мне]». Ведущие пробелы допускаются — так же, как в
# фильтре обработчика ``_RemindPhrase``: иначе фраза с пробелом слева доходила бы
# до парсера, но не разбиралась (``invalid_format``).
_PHRASE_RE = re.compile(
    r"^\s*напомни\b(?:\s+мне\b)?\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Абсолютная ISO «YYYY-MM-DD HH:MM».
_ISO_RE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})\s+(?P<h>\d{1,2}):(?P<m>\d{2})(?P<rest>.*)$",
    re.DOTALL,
)

# Абсолютная «DD.MM[.YYYY] [в] HH:MM».
_DOT_RE = re.compile(
    r"^(?P<d>\d{1,2})\.(?P<mo>\d{1,2})(?:\.(?P<y>\d{4}))?\s+(?:в\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_UNIT_SECONDS = {
    "минуту": 60,
    "минуты": 60,
    "минут": 60,
    "час": 3600,
    "часа": 3600,
    "часов": 3600,
    "день": 86400,
    "дня": 86400,
    "дней": 86400,
}

_HORIZON = timedelta(days=365)


def parse_remind_time(
    raw: str,
    now: datetime,
    timezone: str,
    default_time: str,
) -> ParsedReminder | ParseError:
    """Разбирает текст команды/фразы в aware-UTC момент напоминания.

    Чистая детерминированная функция: текущий момент передаётся аргументом
    ``now`` (aware UTC), обращений к ``datetime.now()``/БД/Telegram нет.

    Args:
        raw: исходный текст команды (``/remind …``) или фразы («напомни …»).
        now: фиксированный текущий момент (aware UTC).
        timezone: IANA-зона пользователя.
        default_time: время по умолчанию «ЧЧ:ММ» для «сегодня/завтра» без
            явного времени.

    Returns:
        :class:`ParsedReminder` при успехе либо :class:`ParseError` с
        соответствующим ``kind``.

    Note:
        Порядок проверок: режим → разбор времени → календарная проверка
        (``date_not_exist``) → DST-валидация (``nonexistent_time`` /
        ``ambiguous_time``) → текст (``empty_text`` / ``text_too_long``) →
        границы (``past`` / ``horizon_exceeded``). DST выполняется ДО
        проверок past/horizon.
    """
    extracted = _extract_time_and_text(raw)
    if isinstance(extracted, ParseError):
        return extracted

    form, params, text = extracted
    resolved = _resolve_time(form, params, now, timezone, default_time)
    if isinstance(resolved, ParseError):
        return resolved
    remind_at_utc = resolved

    cleaned = text.strip()
    if not cleaned:
        return ParseError(kind="empty_text")
    if len(cleaned) > 500:
        return ParseError(kind="text_too_long")
    if remind_at_utc <= now:
        return ParseError(kind="past")
    if remind_at_utc - now > _HORIZON:
        return ParseError(kind="horizon_exceeded")

    return ParsedReminder(remind_at_utc=remind_at_utc, text=cleaned)


def _extract_time_and_text(
    raw: str,
) -> ParseError | tuple[str, dict[str, str | None], str]:
    """Определяет режим разбора и выделяет форму времени и текст.

    Args:
        raw: исходный текст.

    Returns:
        :class:`ParseError` (``invalid_format``), если режим не распознан,
        либо кортеж ``(form, params, text)`` — форма времени, словарь
        параметров и неочищенный текст напоминания.
    """
    if raw.lower().startswith("/remind"):
        return _extract_command(raw)

    phrase = _PHRASE_RE.match(raw)
    if phrase:
        matched = _match_time_at_start(phrase.group("rest"))
        if matched is None:
            return ParseError(kind="invalid_format")
        form, params, rest = matched
        return form, params, _strip_separator(rest)

    return ParseError(kind="invalid_format")


def _extract_command(
    raw: str,
) -> ParseError | tuple[str, dict[str, str | None], str]:
    """Разбирает командный режим ``/remind <время> | <текст>``.

    Args:
        raw: исходный текст команды.

    Returns:
        :class:`ParseError` (``invalid_format``) при отсутствии ``|`` или
        нераспознанной левой части, иначе кортеж формы/параметров/текста.
    """
    body = raw[len("/remind") :]
    body = re.sub(r"^@\S+", "", body).strip()
    if "|" not in body:
        return ParseError(kind="invalid_format")

    left, right = body.split("|", 1)
    matched = _match_time_at_start(left.strip())
    if matched is None:
        return ParseError(kind="invalid_format")

    form, params, rest = matched
    if rest.strip():
        return ParseError(kind="invalid_format")
    return form, params, right


def _match_time_at_start(
    value: str,
) -> tuple[str, dict[str, str | None], str] | None:
    """Сопоставляет форму времени в начале строки.

    Шаблоны применяются от частного к общему. Возвращает форму, словарь
    параметров и остаток строки (возможно, текст напоминания) либо ``None``,
    если ни один шаблон не подошёл.
    """
    match = _RELATIVE_RE.match(value)
    if match:
        return (
            "relative",
            {"n": match.group("n"), "unit": match.group("unit").lower()},
            match.group("rest"),
        )

    match = _DAY_RE.match(value)
    if match:
        form = "today" if match.group("word").lower() == "сегодня" else "tomorrow"
        return (
            form,
            {"h": match.group("h"), "m": match.group("m")},
            match.group("rest"),
        )

    match = _ISO_RE.match(value)
    if match:
        return (
            "iso",
            {
                "y": match.group("y"),
                "mo": match.group("mo"),
                "d": match.group("d"),
                "h": match.group("h"),
                "m": match.group("m"),
            },
            match.group("rest"),
        )

    match = _DOT_RE.match(value)
    if match:
        return (
            "dot",
            {
                "d": match.group("d"),
                "mo": match.group("mo"),
                "y": match.group("y"),
                "h": match.group("h"),
                "m": match.group("m"),
            },
            match.group("rest"),
        )

    return None


def _strip_separator(rest: str) -> str:
    """Удаляет ведущий ``|``-разделитель после выражения времени во фразе."""
    text = rest.strip()
    if text.startswith("|"):
        text = text[1:].strip()
    return text


def _resolve_time(
    form: str,
    params: dict[str, str | None],
    now: datetime,
    timezone: str,
    default_time: str,
) -> datetime | ParseError:
    """Разрешает форму времени в aware-UTC момент с календарной и DST-проверкой.

    Для относительной формы момент считается как ``now + N×unit`` в UTC (без
    локализации). Для абсолютных форм строится локальный naive datetime,
    проверяется календарная корректность (``date_not_exist``) и локализуется
    через зону пользователя с DST-валидацией.
    """
    if form == "relative":
        seconds = _UNIT_SECONDS[params["unit"]] * int(params["n"])  # type: ignore[index]
        # Астрономически большое N (напр. «через 99999999999999999999 дней») не
        # помещается в timedelta — OverflowError; по смыслу это всё «за горизонтом».
        try:
            return now + timedelta(seconds=seconds)
        except OverflowError:
            return ParseError(kind="horizon_exceeded")

    try:
        tz = ZoneInfo(timezone)
    except (KeyError, ValueError):
        return ParseError(kind="invalid_format")

    if form in ("today", "tomorrow"):
        local_now = now.astimezone(tz)
        day = local_now.date()
        if form == "tomorrow":
            day = day + timedelta(days=1)
        moment = _hhmm_time(params, default_time)
        if isinstance(moment, ParseError):
            return moment
        naive = datetime.combine(day, moment)
        return _localize(naive, tz)

    if form == "iso":
        formatted = (
            f"{params['y']}-{params['mo']}-{params['d']} {params['h']}:{params['m']}"
        )
        parsed = _strptime(formatted, "%Y-%m-%d %H:%M")
    else:  # dot
        year = params["y"] or str(now.astimezone(tz).year)
        formatted = f"{params['d']}.{params['mo']}.{year} {params['h']}:{params['m']}"
        parsed = _strptime(formatted, "%d.%m.%Y %H:%M")

    if isinstance(parsed, ParseError):
        return parsed
    return _localize(parsed, tz)


def _hhmm_time(params: dict[str, str | None], default_time: str) -> time | ParseError:
    """Собирает время «HH:MM» из параметров или дефолта.

    Возвращает :class:`ParseError` (``invalid_format``) при некорректном
    времени (например, «25:00»).
    """
    if params["h"] is None or params["m"] is None:
        parsed = _strptime(default_time, "%H:%M")
    else:
        parsed = _strptime(f"{params['h']}:{params['m']}", "%H:%M")
    if isinstance(parsed, ParseError):
        return parsed
    return parsed.time()


def _strptime(value: str, fmt: str) -> datetime | ParseError:
    """Безопасный ``strptime`` с возвратом :class:`ParseError` при ошибке."""
    try:
        return datetime.strptime(value, fmt)
    except ValueError:
        return ParseError(kind="date_not_exist")


def _localize(naive: datetime, tz: ZoneInfo) -> datetime | ParseError:
    """Локализует naive datetime в aware UTC с DST-валидацией.

    Сравнение ``utcoffset`` для ``fold=0``/``fold=1`` выявляет момент перевода
    часов; обратное преобразование отличает провал (``nonexistent_time``) от
    наложения (``ambiguous_time``).
    """
    aware0 = naive.replace(tzinfo=tz, fold=0)
    aware1 = naive.replace(tzinfo=tz, fold=1)
    if aware0.utcoffset() == aware1.utcoffset():
        return aware0.astimezone(UTC)

    roundtrip = aware0.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if roundtrip == naive:
        return ParseError(kind="ambiguous_time")
    return ParseError(kind="nonexistent_time")
