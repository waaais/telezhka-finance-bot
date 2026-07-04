import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import gspread
from google.oauth2.service_account import Credentials

from app.config import Settings
from app.employees import (
    OTHER_EMPLOYEE_BUCKET,
    SPECIAL_EMPLOYEE_BUCKETS,
    employee_bucket,
    normalize_employee_group,
    split_employee_group,
)
from app.parser.models import ParsedScheduleEntry
from app.storage.models import FinanceEntry

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
RU_MONTHS = {
    1: "ЯНВАРЬ",
    2: "ФЕВРАЛЬ",
    3: "МАРТ",
    4: "АПРЕЛЬ",
    5: "МАЙ",
    6: "ИЮНЬ",
    7: "ИЮЛЬ",
    8: "АВГУСТ",
    9: "СЕНТЯБРЬ",
    10: "ОКТЯБРЬ",
    11: "НОЯБРЬ",
    12: "ДЕКАБРЬ",
}

class SheetSync(Protocol):
    async def push_entry(self, entry: FinanceEntry) -> None:
        pass

    async def push_schedule(self, entries: list[ParsedScheduleEntry]) -> None:
        pass

    async def scheduled_employee_for_date(self, entry_date: date) -> str | None:
        pass

    async def aggregate_period(self, start_date: date, end_date: date) -> dict[str, int] | None:
        pass

    async def weekly_salary_breakdown(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, int] | None:
        pass


class DisabledSheetSync:
    async def push_entry(self, entry: FinanceEntry) -> None:
        return None

    async def push_schedule(self, entries: list[ParsedScheduleEntry]) -> None:
        return None

    async def scheduled_employee_for_date(self, entry_date: date) -> str | None:
        return None

    async def aggregate_period(self, start_date: date, end_date: date) -> dict[str, int] | None:
        return None

    async def weekly_salary_breakdown(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, int] | None:
        return None


@dataclass(frozen=True)
class SheetRow:
    row_number: int
    values: list[str]

    @property
    def is_business_columns_empty(self) -> bool:
        business_values = self.values[1:5]
        return all(not str(value).strip() for value in business_values)


@dataclass(frozen=True)
class WeeklySalaryBlock:
    data_start_row: int
    data_end_row: int
    label_rows: dict[str, int]


class GoogleSheetsSync:
    def __init__(self, settings: Settings) -> None:
        if not settings.google_sheets_spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is required when Sheets sync is enabled")

        credentials_file = Path(settings.google_sheets_credentials_file)
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Google service account file not found: {settings.google_sheets_credentials_file}"
            )

        credentials = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        client = gspread.authorize(credentials)
        self.spreadsheet = client.open_by_key(settings.google_sheets_spreadsheet_id)
        self.default_salary = settings.default_salary
        self.low_salary = settings.low_salary
        self.low_salary_names = {name.casefold() for name in settings.low_salary_names}

    async def push_entry(self, entry: FinanceEntry) -> None:
        await asyncio.to_thread(self._push_entry_sync, entry)

    async def push_schedule(self, entries: list[ParsedScheduleEntry]) -> None:
        await asyncio.to_thread(self._push_schedule_sync, entries)

    async def scheduled_employee_for_date(self, entry_date: date) -> str | None:
        return await asyncio.to_thread(self._scheduled_employee_for_date_sync, entry_date)

    async def aggregate_period(self, start_date: date, end_date: date) -> dict[str, int] | None:
        return await asyncio.to_thread(self._aggregate_period_sync, start_date, end_date)

    async def weekly_salary_breakdown(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, int] | None:
        return await asyncio.to_thread(
            self._weekly_salary_breakdown_sync,
            start_date,
            end_date,
        )

    def _push_entry_sync(self, entry: FinanceEntry) -> None:
        worksheet = self._worksheet_for_date(entry.entry_date)
        target_row = self._find_target_row(worksheet, entry.entry_date)
        row_values = [
            _date_to_sheet_number(entry.entry_date),
            entry.employee_name.upper(),
            entry.salary,
            entry.cash,
            entry.cashless,
        ]

        worksheet.update(
            f"A{target_row.row_number}:E{target_row.row_number}",
            [row_values],
            value_input_option="USER_ENTERED",
        )

        self._update_weekly_salary_summary(worksheet, entry.entry_date)

    def _push_schedule_sync(self, entries: list[ParsedScheduleEntry]) -> None:
        for entry in entries:
            worksheet = self._worksheet_for_date(entry.entry_date)
            target_row = self._find_target_row(worksheet, entry.entry_date)
            worksheet.update(
                f"B{target_row.row_number}",
                [[entry.employee_name.upper()]],
                value_input_option="USER_ENTERED",
            )

    def _scheduled_employee_for_date_sync(self, entry_date: date) -> str | None:
        worksheet = self._worksheet_for_date(entry_date)
        target_row = self._find_target_row(worksheet, entry_date)
        employee_name = target_row.values[1].strip()
        if not employee_name:
            return None
        return normalize_employee_group(employee_name)

    def _aggregate_period_sync(self, start_date: date, end_date: date) -> dict[str, int]:
        target_dates = _dates_between(start_date, end_date)
        rows_by_title = self._rows_by_title_for_dates(target_dates)
        return _aggregate_rows_for_dates(rows_by_title, target_dates)

    def _weekly_salary_breakdown_sync(self, start_date: date, end_date: date) -> dict[str, int]:
        target_dates = _dates_between(start_date, end_date)
        rows_by_title = self._rows_by_title_for_dates(target_dates)
        return _weekly_salary_totals_for_dates(
            rows_by_title,
            target_dates,
            default_salary=self.default_salary,
            low_salary=self.low_salary,
            low_salary_names=self.low_salary_names,
        )

    def _worksheet_for_date(self, entry_date: date) -> gspread.Worksheet:
        sheet_title = _sheet_title_for_date(entry_date)
        try:
            return self.spreadsheet.worksheet(sheet_title)
        except gspread.WorksheetNotFound as exc:
            raise RuntimeError(f"Sheet tab not found: {sheet_title}") from exc

    def _rows_by_title_for_dates(self, target_dates: list[date]) -> dict[str, list[list[object]]]:
        rows_by_title: dict[str, list[list[object]]] = {}
        for target_date in target_dates:
            sheet_title = _sheet_title_for_date(target_date)
            if sheet_title in rows_by_title:
                continue
            rows_by_title[sheet_title] = self._worksheet_for_date(target_date).get(
                "A1:H1000",
                value_render_option="UNFORMATTED_VALUE",
            )
        return rows_by_title

    def _sheet_rows_for_dates(self, target_dates: list[date]) -> list[SheetRow]:
        rows_by_title = self._rows_by_title_for_dates(target_dates)
        result: list[SheetRow] = []
        for target_date in target_dates:
            sheet_title = _sheet_title_for_date(target_date)
            rows = rows_by_title.get(sheet_title)
            if rows is None:
                continue
            try:
                result.append(_find_target_row_in_rows(rows, sheet_title, target_date))
            except RuntimeError:
                continue
        return result

    def _find_target_row(self, worksheet: gspread.Worksheet, entry_date: date) -> SheetRow:
        rows = worksheet.get("A1:E1000", value_render_option="UNFORMATTED_VALUE")
        return _find_target_row_in_rows(rows, worksheet.title, entry_date)

    def _update_weekly_salary_summary(
        self,
        worksheet: gspread.Worksheet,
        entry_date: date,
    ) -> None:
        week_dates = _week_dates(entry_date)
        rows_by_title: dict[str, tuple[gspread.Worksheet, list[list[object]]]] = {}
        for week_date in week_dates:
            sheet_title = _sheet_title_for_date(week_date)
            if sheet_title in rows_by_title:
                continue
            try:
                week_worksheet = (
                    worksheet
                    if getattr(worksheet, "title", "") == sheet_title
                    else self._worksheet_for_date(week_date)
                )
            except RuntimeError:
                logger.info(
                    "Sheet tab not found while calculating weekly salary",
                    extra={"sheet": sheet_title, "entry_date": entry_date.isoformat()},
                )
                continue
            rows_by_title[sheet_title] = (
                week_worksheet,
                week_worksheet.get("A1:H1000", value_render_option="UNFORMATTED_VALUE"),
            )

        totals = _weekly_salary_totals_for_dates(
            {title: rows for title, (_week_worksheet, rows) in rows_by_title.items()},
            week_dates,
            default_salary=self.default_salary,
            low_salary=self.low_salary,
            low_salary_names=self.low_salary_names,
        )

        for sheet_title, (week_worksheet, rows) in rows_by_title.items():
            block = _find_weekly_salary_block_for_dates(
                rows,
                [
                    week_date
                    for week_date in week_dates
                    if _sheet_title_for_date(week_date) == sheet_title
                ],
            )
            if block is None:
                logger.info(
                    "Weekly salary block not found for date",
                    extra={"sheet": sheet_title, "entry_date": entry_date.isoformat()},
                )
                continue

            self._write_weekly_salary_totals(week_worksheet, block, totals)

    def _write_weekly_salary_totals(
        self,
        worksheet: gspread.Worksheet,
        block: WeeklySalaryBlock,
        totals: dict[str, int],
    ) -> None:
        row_to_label = {row_number: label for label, row_number in block.label_rows.items()}
        first_row = min(block.label_rows.values())
        last_row = max(block.label_rows.values())
        values = [
            [totals.get(row_to_label.get(row_number, ""), "")]
            for row_number in range(first_row, last_row + 1)
        ]
        worksheet.update(f"H{first_row}:H{last_row}", values, value_input_option="USER_ENTERED")


def create_sheet_sync(settings: Settings) -> SheetSync:
    if not settings.google_sheets_enabled:
        return DisabledSheetSync()
    return GoogleSheetsSync(settings)


def _aggregate_rows_for_dates(
    rows_by_title: dict[str, list[list[object]]],
    target_dates: list[date],
) -> dict[str, int]:
    totals = {
        "cash": 0,
        "cashless": 0,
        "revenue": 0,
        "salaries": 0,
        "profit": 0,
        "entries": 0,
    }
    for target_date in target_dates:
        sheet_title = _sheet_title_for_date(target_date)
        rows = rows_by_title.get(sheet_title)
        if rows is None:
            continue
        try:
            target_row = _find_target_row_in_rows(rows, sheet_title, target_date)
        except RuntimeError:
            continue
        salary = _number(_cell(target_row.values, 2))
        cash = _number(_cell(target_row.values, 3))
        cashless = _number(_cell(target_row.values, 4))
        has_entry = any(value is not None for value in (salary, cash, cashless))
        if not has_entry:
            continue
        totals["cash"] += cash or 0
        totals["cashless"] += cashless or 0
        totals["salaries"] += salary or 0
        totals["entries"] += 1
    totals["revenue"] = totals["cash"] + totals["cashless"]
    totals["profit"] = totals["revenue"] - totals["salaries"]
    return totals


def _date_to_sheet_number(value: date) -> int:
    # Google Sheets stores dates as days since 1899-12-30.
    return (value - date(1899, 12, 30)).days


def _sheet_title_for_date(value: date) -> str:
    return f"{RU_MONTHS[value.month]} {value.year}"


def _looks_like_same_date(value: object, target_serial: int) -> bool:
    if isinstance(value, int | float):
        return int(value) == target_serial

    text = str(value).strip()
    if not text:
        return False
    if text.replace(".", "", 1).isdigit():
        return int(float(text)) == target_serial

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return _date_to_sheet_number(datetime.strptime(text, fmt).date()) == target_serial
        except ValueError:
            continue
    return False


def _week_dates(entry_date: date) -> list[date]:
    week_start = entry_date - timedelta(days=entry_date.weekday())
    return [week_start + timedelta(days=offset) for offset in range(7)]


def _dates_between(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    if days < 0:
        return []
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _find_target_row_in_rows(
    rows: list[list[object]],
    sheet_title: str,
    entry_date: date,
) -> SheetRow:
    target_serial = _date_to_sheet_number(entry_date)
    for index, values in enumerate(rows, start=1):
        if not values:
            continue
        first_value = values[0]
        if _looks_like_same_date(first_value, target_serial):
            normalized_values = [str(value) if value is not None else "" for value in values]
            normalized_values.extend([""] * (5 - len(normalized_values)))
            return SheetRow(row_number=index, values=normalized_values[:5])
    raise RuntimeError(f"Date row not found in {sheet_title}: {entry_date.isoformat()}")


def _find_weekly_salary_block_for_dates(
    rows: list[list[object]],
    entry_dates: list[date],
) -> WeeklySalaryBlock | None:
    for entry_date in entry_dates:
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(entry_date))
        if block is not None:
            return block
    return None


def _find_weekly_salary_block(
    rows: list[list[object]],
    target_serial: int,
) -> WeeklySalaryBlock | None:
    header_indexes = [
        index for index, row in enumerate(rows) if _normalize_text(_cell(row, 6)) == "з/п"
    ]

    for header_position, header_index in enumerate(header_indexes):
        next_header_index = (
            header_indexes[header_position + 1]
            if header_position + 1 < len(header_indexes)
            else len(rows)
        )

        label_rows: dict[str, int] = {}
        for label_index in range(header_index + 1, next_header_index):
            label = _weekly_label(_cell(rows[label_index], 6))
            if label is None:
                continue
            label_rows[label] = label_index + 1
            if label == OTHER_EMPLOYEE_BUCKET:
                break

        if not label_rows:
            continue

        data_start_row = header_index + 1
        data_end_row = next_header_index
        if _date_is_inside_rows(rows, target_serial, data_start_row, data_end_row):
            return WeeklySalaryBlock(
                data_start_row=data_start_row,
                data_end_row=data_end_row,
                label_rows=label_rows,
            )
    return None


def _weekly_salary_totals(
    rows: list[list[object]],
    block: WeeklySalaryBlock,
    *,
    default_salary: int = 2500,
    low_salary: int = 2000,
    low_salary_names: set[str] | None = None,
) -> dict[str, int]:
    totals = _empty_weekly_salary_totals()
    low_salary_names = low_salary_names or set(SPECIAL_EMPLOYEE_BUCKETS)

    for row_number in range(block.data_start_row, block.data_end_row + 1):
        row = rows[row_number - 1] if row_number - 1 < len(rows) else []
        employee_name = _cell(row, 1)
        salary = _number(_cell(row, 2))
        if not employee_name or salary is None:
            continue

        _add_salary_to_totals(
            totals,
            str(employee_name),
            salary,
            default_salary=default_salary,
            low_salary=low_salary,
            low_salary_names=low_salary_names,
        )
    return totals


def _weekly_salary_totals_for_dates(
    rows_by_title: dict[str, list[list[object]]],
    entry_dates: list[date],
    *,
    default_salary: int = 2500,
    low_salary: int = 2000,
    low_salary_names: set[str] | None = None,
) -> dict[str, int]:
    totals = _empty_weekly_salary_totals()
    low_salary_names = low_salary_names or set(SPECIAL_EMPLOYEE_BUCKETS)

    for entry_date in entry_dates:
        sheet_title = _sheet_title_for_date(entry_date)
        rows = rows_by_title.get(sheet_title)
        if rows is None:
            continue
        try:
            target_row = _find_target_row_in_rows(rows, sheet_title, entry_date)
        except RuntimeError:
            continue
        employee_name = _cell(target_row.values, 1)
        salary = _number(_cell(target_row.values, 2))
        if not employee_name or salary is None:
            continue
        _add_salary_to_totals(
            totals,
            str(employee_name),
            salary,
            default_salary=default_salary,
            low_salary=low_salary,
            low_salary_names=low_salary_names,
        )
    return totals


def _empty_weekly_salary_totals() -> dict[str, int]:
    totals = {label: 0 for label in SPECIAL_EMPLOYEE_BUCKETS.values()}
    totals[OTHER_EMPLOYEE_BUCKET] = 0
    return totals


def _add_salary_to_totals(
    totals: dict[str, int],
    employee_name: str,
    salary: int,
    *,
    default_salary: int,
    low_salary: int,
    low_salary_names: set[str],
) -> None:
    for bucket, part_salary in _split_salary_by_employee_group(
        employee_name,
        salary,
        default_salary=default_salary,
        low_salary=low_salary,
        low_salary_names=low_salary_names,
    ):
        totals[bucket] += part_salary


def _date_is_inside_rows(
    rows: list[list[object]],
    target_serial: int,
    start_row: int,
    end_row: int,
) -> bool:
    for row_number in range(start_row, end_row + 1):
        row = rows[row_number - 1] if row_number - 1 < len(rows) else []
        if _looks_like_same_date(_cell(row, 0), target_serial):
            return True
    return False


def _employee_bucket(value: object) -> str:
    return employee_bucket(str(value))


def _split_salary_by_employee_group(
    employee_name: str,
    total_salary: int,
    *,
    default_salary: int,
    low_salary: int,
    low_salary_names: set[str],
) -> list[tuple[str, int]]:
    employees = split_employee_group(employee_name)
    if not employees:
        return [(OTHER_EMPLOYEE_BUCKET, total_salary)]
    if len(employees) == 1:
        return [(employee_bucket(employees[0]), total_salary)]

    planned_salaries = [
        low_salary if employee.casefold() in low_salary_names else default_salary
        for employee in employees
    ]
    planned_total = sum(planned_salaries)
    if planned_total <= 0:
        return [(employee_bucket(employee), 0) for employee in employees]
    if planned_total == total_salary:
        return [
            (employee_bucket(employee), salary)
            for employee, salary in zip(employees, planned_salaries, strict=True)
        ]
    if total_salary < planned_total:
        return [(employee_bucket(employees[0]), total_salary)]

    distributed: list[tuple[str, int]] = []
    remaining = total_salary
    for index, employee in enumerate(employees):
        if index == len(employees) - 1:
            part_salary = remaining
        else:
            part_salary = round(total_salary * planned_salaries[index] / planned_total)
            remaining -= part_salary
        distributed.append((employee_bucket(employee), part_salary))
    return distributed


def _weekly_label(value: object) -> str | None:
    normalized = _normalize_text(value)
    if normalized == OTHER_EMPLOYEE_BUCKET:
        return OTHER_EMPLOYEE_BUCKET
    return SPECIAL_EMPLOYEE_BUCKETS.get(normalized)


def _normalize_text(value: object) -> str:
    return str(value).strip().casefold()


def _cell(row: list[object], index: int) -> object | None:
    if index >= len(row):
        return None
    return row[index]


def _number(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return int(value)

    normalized = (
        str(value)
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace(",", ".")
        .strip()
    )
    if not normalized:
        return None
    try:
        return int(float(normalized))
    except ValueError:
        return None
