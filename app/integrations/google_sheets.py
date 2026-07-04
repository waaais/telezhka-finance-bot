import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
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


class DisabledSheetSync:
    async def push_entry(self, entry: FinanceEntry) -> None:
        return None

    async def push_schedule(self, entries: list[ParsedScheduleEntry]) -> None:
        return None

    async def scheduled_employee_for_date(self, entry_date: date) -> str | None:
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

    def _worksheet_for_date(self, entry_date: date) -> gspread.Worksheet:
        sheet_title = _sheet_title_for_date(entry_date)
        try:
            return self.spreadsheet.worksheet(sheet_title)
        except gspread.WorksheetNotFound as exc:
            raise RuntimeError(f"Sheet tab not found: {sheet_title}") from exc

    def _find_target_row(self, worksheet: gspread.Worksheet, entry_date: date) -> SheetRow:
        target_serial = _date_to_sheet_number(entry_date)
        rows = worksheet.get("A1:E1000", value_render_option="UNFORMATTED_VALUE")
        for index, values in enumerate(rows, start=1):
            if not values:
                continue
            first_value = values[0]
            if _looks_like_same_date(first_value, target_serial):
                normalized_values = [str(value) if value is not None else "" for value in values]
                normalized_values.extend([""] * (5 - len(normalized_values)))
                return SheetRow(row_number=index, values=normalized_values[:5])
        raise RuntimeError(f"Date row not found in {worksheet.title}: {entry_date.isoformat()}")

    def _update_weekly_salary_summary(
        self,
        worksheet: gspread.Worksheet,
        entry_date: date,
    ) -> None:
        rows = worksheet.get("A1:H1000", value_render_option="UNFORMATTED_VALUE")
        target_serial = _date_to_sheet_number(entry_date)
        block = _find_weekly_salary_block(rows, target_serial)
        if block is None:
            logger.info(
                "Weekly salary block not found for date",
                extra={"sheet": worksheet.title, "entry_date": entry_date.isoformat()},
            )
            return

        totals = _weekly_salary_totals(
            rows,
            block,
            default_salary=self.default_salary,
            low_salary=self.low_salary,
            low_salary_names=self.low_salary_names,
        )
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
    totals = {label: 0 for label in SPECIAL_EMPLOYEE_BUCKETS.values()}
    totals[OTHER_EMPLOYEE_BUCKET] = 0
    low_salary_names = low_salary_names or set(SPECIAL_EMPLOYEE_BUCKETS)

    for row_number in range(block.data_start_row, block.data_end_row + 1):
        row = rows[row_number - 1] if row_number - 1 < len(rows) else []
        employee_name = _cell(row, 1)
        salary = _number(_cell(row, 2))
        if not employee_name or salary is None:
            continue

        for bucket, part_salary in _split_salary_by_employee_group(
            str(employee_name),
            salary,
            default_salary=default_salary,
            low_salary=low_salary,
            low_salary_names=low_salary_names,
        ):
            totals[bucket] += part_salary
    return totals


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
