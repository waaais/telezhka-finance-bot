import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.employees import normalize_employee_group, normalize_employee_name, split_employee_group
from app.parser.models import (
    ParsedFinanceCorrection,
    ParsedFinanceMessage,
    ParsedNoWorkMessage,
    ParsedScheduleEntry,
    ParsedScheduleMessage,
    ParseError,
)

MONTHS = {
    "января": 1,
    "январь": 1,
    "февраля": 2,
    "февраль": 2,
    "марта": 3,
    "март": 3,
    "апреля": 4,
    "апрель": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июля": 7,
    "июль": 7,
    "августа": 8,
    "август": 8,
    "сентября": 9,
    "сентябрь": 9,
    "октября": 10,
    "октябрь": 10,
    "ноября": 11,
    "ноябрь": 11,
    "декабря": 12,
    "декабрь": 12,
}

WEEKDAYS = {
    "пн": 0,
    "понедельник": 0,
    "понедельника": 0,
    "вт": 1,
    "вторник": 1,
    "вторника": 1,
    "ср": 2,
    "среда": 2,
    "среду": 2,
    "среды": 2,
    "чт": 3,
    "четверг": 3,
    "четверга": 3,
    "пт": 4,
    "пятница": 4,
    "пятницу": 4,
    "пятницы": 4,
    "сб": 5,
    "суббота": 5,
    "субботу": 5,
    "субботы": 5,
    "вс": 6,
    "воскресенье": 6,
    "воскресенья": 6,
}

ALIAS_PREFIX = r"(?<![А-ЯЁа-яёA-Za-z])"
CASH_ALIASES = ALIAS_PREFIX + r"(?:наличка|наличку|наличные|нал|кэш|cash)"
CASHLESS_ALIASES = (
    ALIAS_PREFIX + r"(?:безналичные|безнал|картой|карта|терминал|эквайринг|card)"
)
EMPLOYEE_FIELD_ALIASES = r"(?:продавц[а-яё]*|продаца|сотрудник[а-яё]*|имя)"
NAME_PATTERN = re.compile(r"[А-ЯЁа-яёA-Za-z][а-яёa-zA-ZА-ЯЁ-]{1,40}")
SCHEDULE_LINE_PATTERN = re.compile(
    r"^\s*(?:[а-яё]{2}\.?\s+)?"
    r"(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{2,4}))?"
    r"\s*(?:—|–|-|:)\s*(?P<names>.+?)\s*$",
    re.IGNORECASE,
)
EDIT_WORDS = {
    "измени",
    "изменить",
    "изменяем",
    "поменяй",
    "поменять",
    "исправь",
    "исправить",
    "замени",
    "заменить",
    "обнови",
    "обновить",
}
ISO_DATE_PATTERN = re.compile(
    r"(?:^|\s)(?P<date>(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{2,4}))?)(?:\s|$)"
)
RU_DATE_PATTERN = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>"
    + "|".join(MONTHS.keys())
    + r")(?:\s+(?P<year>\d{2,4}))?\b",
    re.IGNORECASE,
)
TODAY_PATTERN = re.compile(r"\b(?:сегодня|сегодн[яа]|сегорлня|сеголня)\b", re.IGNORECASE)
AFTER_TOMORROW_PATTERN = re.compile(r"\b(?:послезавтра|после\s+завтра)\b", re.IGNORECASE)
TOMORROW_PATTERN = re.compile(r"\b(?:завтра|завтро)\b", re.IGNORECASE)
YESTERDAY_PATTERN = re.compile(r"\bвчера\b", re.IGNORECASE)
WEEKDAY_PATTERN = re.compile(
    r"(?:\bза\s+)?\b(?P<weekday>"
    + "|".join(WEEKDAYS.keys())
    + r")(?:\.(?=\s|$)|\b)",
    re.IGNORECASE,
)


def parse_finance_message(
    text: str,
    *,
    now: date,
    timezone: str,
    allow_missing_employee: bool = False,
) -> ParsedFinanceMessage:
    normalized = _normalize_text(text)
    if not normalized:
        raise ParseError("Не вижу данных. Пришлите, например: `Ксюша нал 12500 безнал 38640`.")

    entry_date, without_date = _extract_date(normalized, now)
    cash = _extract_amount(without_date, CASH_ALIASES, "наличку")
    cashless = _extract_amount(without_date, CASHLESS_ALIASES, "безнал")
    employee_name = _extract_employee_name(
        without_date,
        allow_missing=allow_missing_employee,
    )

    return ParsedFinanceMessage(
        employee_name=employee_name,
        entry_date=entry_date,
        cash=cash,
        cashless=cashless,
        raw_text=text,
    )


def looks_like_schedule(text: str) -> bool:
    return len(_parse_schedule_lines(text, now=date.today(), strict=False)) >= 1


def parse_schedule_message(text: str, *, now: date) -> ParsedScheduleMessage:
    entries = _parse_schedule_lines(text, now=now, strict=True)
    if not entries:
        raise ParseError(
            "Не смог прочитать расписание. Формат строки: `пн. 29.06 — Ксюша`."
        )
    return ParsedScheduleMessage(entries=entries, raw_text=text)


def looks_like_no_work(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    return bool(re.search(r"\b(?:не\s+работаем|выходной|закрыто)\b", normalized))


def parse_no_work_message(text: str, *, now: date) -> ParsedNoWorkMessage:
    normalized = _normalize_text(text)
    entry_date, _without_date = _extract_date(normalized, now)
    return ParsedNoWorkMessage(entry_date=entry_date, raw_text=text)


def looks_like_correction(text: str) -> bool:
    lowered = _normalize_text(text).casefold()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in EDIT_WORDS)


def parse_finance_correction(text: str, *, now: date, timezone: str) -> ParsedFinanceCorrection:
    normalized = _normalize_text(text)
    if not normalized:
        raise ParseError("Не вижу данных для изменения.")

    entry_date, without_date = _extract_date(normalized, now)
    cash = _extract_optional_correction_amount(without_date, CASH_ALIASES)
    cashless = _extract_optional_correction_amount(without_date, CASHLESS_ALIASES)
    new_employee_name, without_employee_change = _extract_new_employee_name(without_date)
    if cash is None and cashless is None and new_employee_name is None:
        raise ParseError(
            "Что изменить: наличку, безнал или продавца? Например: "
            "`измени наличку за 2 июля на 19000` или `измени продавца за 2 июля на Дима`."
        )

    employee_name = _extract_optional_employee_name(without_employee_change)
    return ParsedFinanceCorrection(
        entry_date=entry_date,
        employee_name=employee_name,
        new_employee_name=new_employee_name,
        cash=cash,
        cashless=cashless,
        raw_text=text,
    )


def current_date(timezone: str) -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo(timezone)).date()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _extract_date(text: str, now: date) -> tuple[date, str]:
    today_match = TODAY_PATTERN.search(text)
    if today_match:
        return now, TODAY_PATTERN.sub(" ", text, count=1)
    after_tomorrow_match = AFTER_TOMORROW_PATTERN.search(text)
    if after_tomorrow_match:
        return now + timedelta(days=2), AFTER_TOMORROW_PATTERN.sub(" ", text, count=1)
    tomorrow_match = TOMORROW_PATTERN.search(text)
    if tomorrow_match:
        return now + timedelta(days=1), TOMORROW_PATTERN.sub(" ", text, count=1)
    yesterday_match = YESTERDAY_PATTERN.search(text)
    if yesterday_match:
        return now - timedelta(days=1), YESTERDAY_PATTERN.sub(" ", text, count=1)

    iso_match = ISO_DATE_PATTERN.search(text)
    if iso_match:
        parsed = _build_date(
            int(iso_match.group("day")),
            int(iso_match.group("month")),
            iso_match.group("year"),
            now,
        )
        start, end = iso_match.span("date")
        return parsed, text[:start] + " " + text[end:]

    ru_match = RU_DATE_PATTERN.search(text)
    if ru_match:
        parsed = _build_date(
            int(ru_match.group("day")),
            MONTHS[ru_match.group("month").lower()],
            ru_match.group("year"),
            now,
        )
        return parsed, text[: ru_match.start()] + " " + text[ru_match.end() :]

    weekday_match = WEEKDAY_PATTERN.search(text)
    if weekday_match:
        weekday = WEEKDAYS[weekday_match.group("weekday").casefold()]
        days_ago = (now.weekday() - weekday) % 7
        parsed = now - timedelta(days=days_ago)
        return parsed, text[: weekday_match.start()] + " " + text[weekday_match.end() :]

    return now, text


def _build_date(day: int, month: int, year_text: str | None, now: date) -> date:
    year = now.year
    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ParseError("Дата выглядит некорректно. Проверьте день и месяц.") from exc


def _extract_amount(text: str, alias_pattern: str, human_name: str) -> int:
    pattern = re.compile(
        rf"{alias_pattern}\s*[:=-]?\s*(?P<amount>\d[\d\s.,]*)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise ParseError(f"Не нашел {human_name}. Пример: `нал 12500 безнал 38640`.")

    amount_text = match.group("amount")
    digits = re.sub(r"[^\d]", "", amount_text)
    if not digits:
        raise ParseError(f"Не смог прочитать сумму для {human_name}.")

    amount = int(digits)
    if amount < 0:
        raise ParseError(f"Сумма для {human_name} не может быть отрицательной.")
    if amount > 100_000_000:
        raise ParseError(f"Сумма для {human_name} слишком большая, проверьте ввод.")
    return amount


def _extract_optional_correction_amount(text: str, alias_pattern: str) -> int | None:
    pattern = re.compile(
        rf"{alias_pattern}\b[^\d]*(?P<amount>\d[\d\s.,]*)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None

    return _parse_amount(match.group("amount"), "суммы")


def _extract_new_employee_name(text: str) -> tuple[str | None, str]:
    pattern = re.compile(
        rf"\b{EMPLOYEE_FIELD_ALIASES}\b.*?(?:\bна\b|=|:)\s*(?P<employee>.+?)\s*$",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None, text

    employee_name = _extract_employee_group(match.group("employee"))
    if not employee_name:
        return None, text
    return employee_name, text[: match.start()] + " " + text[match.end() :]


def _parse_amount(amount_text: str, human_name: str) -> int:
    digits = re.sub(r"[^\d]", "", amount_text)
    if not digits:
        raise ParseError(f"Не смог прочитать {human_name}.")

    amount = int(digits)
    if amount < 0:
        raise ParseError(f"{human_name.capitalize()} не может быть отрицательной.")
    if amount > 100_000_000:
        raise ParseError(f"{human_name.capitalize()} слишком большая, проверьте ввод.")
    return amount


def _extract_employee_name(text: str, *, allow_missing: bool = False) -> str:
    cleaned = re.sub(rf"{CASH_ALIASES}\s*[:=-]?\s*\d[\d\s.,]*", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(
        rf"{CASHLESS_ALIASES}\s*[:=-]?\s*\d[\d\s.,]*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    employee_group = _extract_employee_group(cleaned)
    if not employee_group:
        if allow_missing:
            return ""
        raise ParseError("Не нашел имя сотрудника. Пример: `Ксюша нал 12500 безнал 38640`.")

    return employee_group


def _extract_optional_employee_name(text: str) -> str | None:
    cleaned = _remove_amount_phrases(text)
    cleaned = re.sub(
        rf"\b{EMPLOYEE_FIELD_ALIASES}\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:"
        + "|".join(re.escape(word) for word in sorted(EDIT_WORDS))
        + r"|за|на|день|дату|строку|запись)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _extract_employee_group(cleaned)


def _remove_amount_phrases(text: str) -> str:
    cleaned = re.sub(
        rf"{CASH_ALIASES}\b[^\d]*(?:\d[\d\s.,]*)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"{CASHLESS_ALIASES}\b[^\d]*(?:\d[\d\s.,]*)?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _extract_employee_group(text: str) -> str | None:
    ignored = {
        "нал",
        "безнал",
        "cash",
        "card",
        "сегодня",
        "сегодняа",
        "сегорлня",
        "сеголня",
        "вчера",
        "завтра",
        "завтро",
        "послезавтра",
        "после",
        "выручка",
        "за",
        "день",
        "смена",
        "смену",
    } | set(WEEKDAYS.keys())
    words = NAME_PATTERN.findall(text)
    if not words and "&" not in text:
        return None

    if "+" in text:
        group = normalize_employee_group(text)
        return group or None

    for word in words:
        if word.lower() not in ignored:
            return normalize_employee_name(word)
    return None


def _parse_schedule_lines(text: str, *, now: date, strict: bool) -> list[ParsedScheduleEntry]:
    entries: list[ParsedScheduleEntry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = SCHEDULE_LINE_PATTERN.match(stripped)
        if not match:
            continue

        names_text = match.group("names").replace("?", "&")
        employee_names = split_employee_group(names_text)
        if not employee_names:
            if strict:
                raise ParseError(f"Не нашел сотрудника в строке: `{stripped}`.")
            continue

        try:
            entry_date = _build_date(
                int(match.group("day")),
                int(match.group("month")),
                match.group("year"),
                now,
            )
        except ParseError:
            if strict:
                raise
            continue

        entries.append(
            ParsedScheduleEntry(
                entry_date=entry_date,
                employee_name="+".join(employee_names),
            )
        )
    return entries
