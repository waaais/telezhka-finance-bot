from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Period:
    name: str
    start: date
    end: date


def current_week(today: date) -> Period:
    start = today - timedelta(days=today.weekday())
    return Period(name="неделю", start=start, end=today)


def current_month(today: date) -> Period:
    start = today.replace(day=1)
    return Period(name="месяц", start=start, end=today)

