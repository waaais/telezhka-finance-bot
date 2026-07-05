import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.employees import OTHER_EMPLOYEE_BUCKET, employee_bucket, normalize_employee_group, split_employee_group
from app.integrations.evotor import EvotorSync
from app.integrations.google_sheets import SheetSync
from app.parser.finance_parser import (
    looks_like_no_work,
    looks_like_correction,
    looks_like_schedule,
    parse_finance_correction,
    parse_finance_message,
    parse_no_work_message,
    parse_schedule_message,
)
from app.parser.models import ParsedFinanceMessage, ParseError
from app.retry import retry_db
from app.salary.engine import SalaryEngine
from app.statistics.engine import Period, current_month, current_week
from app.storage.database import session_scope
from app.storage.models import FinanceEntry
from app.storage.repositories import EmployeeRepository, FinanceRepository, ReminderChatRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessedFinanceResult:
    entry: FinanceEntry | None
    duplicate: bool
    parse_error: str | None = None
    sheet_error: str | None = None
    updated: bool = False
    response_text: str | None = None
    schedule_count: int = 0


class FinanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        salary_engine: SalaryEngine,
        sheet_sync: SheetSync,
        evotor_sync: EvotorSync,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.salary_engine = salary_engine
        self.sheet_sync = sheet_sync
        self.evotor_sync = evotor_sync

    async def seed_default_employees(self) -> None:
        async def operation() -> None:
            async with session_scope(self.session_factory) as session:
                employees = EmployeeRepository(session)
                for name in self.settings.low_salary_names:
                    await employees.upsert(name, self.settings.low_salary)

        await retry_db(operation)

    async def seed_known_reminder_chats(self) -> None:
        async def operation() -> None:
            async with session_scope(self.session_factory) as session:
                reminders = ReminderChatRepository(session)
                await reminders.seed_from_processed_messages()
                for chat_id in self.settings.admin_ids:
                    await reminders.enable(chat_id)

        await retry_db(operation)

    async def remember_chat(self, chat_id: int) -> None:
        async def operation() -> None:
            async with session_scope(self.session_factory) as session:
                await ReminderChatRepository(session).enable(chat_id)

        await retry_db(operation)

    async def reminder_chat_ids(self) -> list[int]:
        async def operation() -> list[int]:
            async with session_scope(self.session_factory) as session:
                return await ReminderChatRepository(session).enabled_chat_ids()

        return await retry_db(operation)

    async def process_text_message(
        self,
        *,
        text: str,
        chat_id: int,
        message_id: int,
        today: date,
    ) -> ProcessedFinanceResult:
        if looks_like_schedule(text):
            return await self._process_schedule_message(text=text, today=today)

        if looks_like_no_work(text):
            return await self._process_no_work_message(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                today=today,
            )

        if looks_like_correction(text):
            return await self._process_correction_message(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                today=today,
            )

        try:
            parsed = parse_finance_message(
                text,
                now=today,
                timezone=self.settings.timezone,
                allow_missing_employee=True,
            )
            if not parsed.employee_name:
                scheduled_employee = await self.sheet_sync.scheduled_employee_for_date(
                    parsed.entry_date
                )
                if not scheduled_employee:
                    raise ParseError(
                        f"На {parsed.entry_date:%d.%m.%Y} в расписании нет сотрудника. "
                        "Напишите имя в сообщении или сначала внесите расписание."
                    )
                parsed = ParsedFinanceMessage(
                    employee_name=normalize_employee_group(scheduled_employee),
                    entry_date=parsed.entry_date,
                    cash=parsed.cash,
                    cashless=parsed.cashless,
                    raw_text=text,
                )
        except ParseError as exc:
            await self._remember_parse_error(
                chat_id=chat_id,
                message_id=message_id,
                raw_text=text,
                error_text=exc.public_message,
            )
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=exc.public_message,
            )
        except Exception as exc:
            logger.exception("Failed to read scheduled employee for finance entry")
            error_text = (
                "Не смог прочитать сотрудника из расписания в таблице. "
                "Напишите имя в сообщении или проверьте Google Sheets."
            )
            await self._remember_parse_error(
                chat_id=chat_id,
                message_id=message_id,
                raw_text=text,
                error_text=error_text,
            )
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=error_text,
                sheet_error=str(exc),
            )

        async def operation() -> ProcessedFinanceResult:
            async with session_scope(self.session_factory) as session:
                employees = EmployeeRepository(session)
                salary = await self._salary_for_employee_group(employees, parsed.employee_name)
                employee = await employees.get_or_create(parsed.employee_name, salary)
                stored = await FinanceRepository(session).store_entry_once(
                    parsed,
                    employee,
                    salary,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return ProcessedFinanceResult(
                    entry=stored.entry,
                    duplicate=stored.duplicate,
                    parse_error=stored.public_error,
                    updated=stored.status == "updated",
                )

        result = await retry_db(operation)
        if result.entry is None or result.duplicate:
            return result

        try:
            await self.sheet_sync.push_entry(result.entry)
        except Exception as exc:
            logger.exception(
                "Failed to sync finance entry to Google Sheets",
                extra={"entry_id": result.entry.id},
            )
            return ProcessedFinanceResult(
                entry=result.entry,
                duplicate=False,
                parse_error=None,
                sheet_error=str(exc),
                updated=result.updated,
            )

        return result

    async def _process_schedule_message(
        self,
        *,
        text: str,
        today: date,
    ) -> ProcessedFinanceResult:
        try:
            schedule = parse_schedule_message(text, now=today)
        except ParseError as exc:
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=exc.public_message,
            )

        try:
            await self.sheet_sync.push_schedule(schedule.entries)
        except Exception as exc:
            logger.exception("Failed to sync schedule to Google Sheets")
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                sheet_error=str(exc),
                response_text=(
                    "⚠️ Расписание прочитал, но не смог отправить в Google Sheets. "
                    "Ошибка записана в лог."
                ),
                schedule_count=len(schedule.entries),
            )

        return ProcessedFinanceResult(
            entry=None,
            duplicate=False,
            schedule_count=len(schedule.entries),
            response_text=f"✅ Внес расписание в таблицу: {len(schedule.entries)} дней.",
        )

    async def _process_no_work_message(
        self,
        *,
        text: str,
        chat_id: int,
        message_id: int,
        today: date,
    ) -> ProcessedFinanceResult:
        try:
            parsed_no_work = parse_no_work_message(text, now=today)
            scheduled_employee = await self.sheet_sync.scheduled_employee_for_date(
                parsed_no_work.entry_date
            )
        except Exception as exc:
            logger.exception("Failed to read scheduled employee for no-work day")
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=(
                    "Не смог прочитать сотрудника из расписания в таблице. "
                    "Сначала внесите расписание или напишите день целиком."
                ),
                sheet_error=str(exc),
            )

        if not scheduled_employee:
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=(
                    f"На {parsed_no_work.entry_date:%d.%m.%Y} в расписании нет сотрудника. "
                    "Сначала внесите расписание."
                ),
            )

        parsed = ParsedFinanceMessage(
            employee_name=normalize_employee_group(scheduled_employee),
            entry_date=parsed_no_work.entry_date,
            cash=0,
            cashless=0,
            raw_text=text,
        )

        async def operation() -> ProcessedFinanceResult:
            async with session_scope(self.session_factory) as session:
                employees = EmployeeRepository(session)
                employee = await employees.get_or_create(parsed.employee_name, 0)
                stored = await FinanceRepository(session).store_entry_once(
                    parsed,
                    employee,
                    0,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return ProcessedFinanceResult(
                    entry=stored.entry,
                    duplicate=stored.duplicate,
                    parse_error=stored.public_error,
                    updated=stored.status == "updated",
                )

        result = await retry_db(operation)
        if result.entry is None or result.duplicate:
            return result

        try:
            await self.sheet_sync.push_entry(result.entry)
        except Exception as exc:
            logger.exception(
                "Failed to sync no-work entry to Google Sheets",
                extra={"entry_id": result.entry.id},
            )
            return ProcessedFinanceResult(
                entry=result.entry,
                duplicate=False,
                sheet_error=str(exc),
                updated=result.updated,
            )

        return result

    async def _process_correction_message(
        self,
        *,
        text: str,
        chat_id: int,
        message_id: int,
        today: date,
    ) -> ProcessedFinanceResult:
        try:
            correction = parse_finance_correction(text, now=today, timezone=self.settings.timezone)
        except ParseError as exc:
            await self._remember_parse_error(
                chat_id=chat_id,
                message_id=message_id,
                raw_text=text,
                error_text=exc.public_message,
            )
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=exc.public_message,
            )

        async def operation() -> ProcessedFinanceResult:
            async with session_scope(self.session_factory) as session:
                finance_entries = FinanceRepository(session)
                stored = await finance_entries.update_entry_once(
                    correction,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                if stored.entry is not None and not stored.duplicate:
                    employees = EmployeeRepository(session)
                    if correction.new_employee_name:
                        stored.entry.employee_name = normalize_employee_group(
                            correction.new_employee_name
                        )
                    salary = await self._salary_for_employee_group(
                        employees,
                        stored.entry.employee_name,
                    )
                    employee = await employees.upsert(stored.entry.employee_name, salary)
                    stored.entry.employee_id = employee.id
                    stored.entry.employee_name = employee.name
                    stored.entry.salary = salary
                    await session.flush()
                return ProcessedFinanceResult(
                    entry=stored.entry,
                    duplicate=stored.duplicate,
                    parse_error=stored.public_error,
                    updated=stored.status == "updated",
                )

        result = await retry_db(operation)
        if result.entry is None or result.duplicate:
            return result

        try:
            await self.sheet_sync.push_entry(result.entry)
        except Exception as exc:
            logger.exception(
                "Failed to sync corrected finance entry to Google Sheets",
                extra={"entry_id": result.entry.id},
            )
            return ProcessedFinanceResult(
                entry=result.entry,
                duplicate=False,
                parse_error=None,
                sheet_error=str(exc),
                updated=True,
            )

        return result

    async def _remember_parse_error(
        self,
        *,
        chat_id: int,
        message_id: int,
        raw_text: str,
        error_text: str,
    ) -> None:
        async def operation() -> None:
            async with session_scope(self.session_factory) as session:
                await FinanceRepository(session).remember_failed_message(
                    chat_id=chat_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    error_text=error_text,
                )

        try:
            await retry_db(operation)
        except Exception:
            logger.exception("Failed to remember parse error")

    async def statistics_for_week(self, today: date) -> tuple[Period, dict[str, int]]:
        return await self._statistics(current_week(today))

    async def statistics_for_month(self, today: date) -> tuple[Period, dict[str, int]]:
        return await self._statistics(current_month(today))

    async def _statistics(self, period: Period) -> tuple[Period, dict[str, int]]:
        try:
            sheet_totals = await self.sheet_sync.aggregate_period(period.start, period.end)
        except Exception as exc:
            logger.exception(
                "Failed to aggregate statistics from Google Sheets",
                extra={
                    "period_start": period.start.isoformat(),
                    "period_end": period.end.isoformat(),
                },
            )
            if self.settings.google_sheets_enabled:
                raise RuntimeError("Не смог прочитать отчет из Google Sheets.") from exc
            sheet_totals = None
        if sheet_totals is not None:
            return period, sheet_totals

        async def operation() -> dict[str, int]:
            async with session_scope(self.session_factory) as session:
                return await FinanceRepository(session).aggregate(period.start, period.end)

        return period, await retry_db(operation)

    async def has_finance_entry(self, chat_id: int, entry_date: date) -> bool:
        try:
            sheet_totals = await self.sheet_sync.aggregate_period(entry_date, entry_date)
        except Exception:
            logger.exception(
                "Failed to check finance entry in Google Sheets",
                extra={"entry_date": entry_date.isoformat()},
            )
            sheet_totals = None
        if sheet_totals is not None and sheet_totals.get("entries", 0) > 0:
            return True

        async def operation() -> bool:
            async with session_scope(self.session_factory) as session:
                return await FinanceRepository(session).has_entry_for_chat_date(chat_id, entry_date)

        return await retry_db(operation)

    async def import_evotor_revenue(
        self,
        *,
        chat_id: int,
        message_id: int,
        today: date,
        skip_if_exists: bool = False,
    ) -> ProcessedFinanceResult:
        if skip_if_exists and await self.has_finance_entry(chat_id, today):
            return ProcessedFinanceResult(
                entry=None,
                duplicate=True,
                response_text="☑️ Выручка за сегодня уже есть в таблице, Эвотор не трогаю.",
            )

        try:
            evotor_revenue = await self.evotor_sync.fetch_revenue(today)
        except Exception as exc:
            logger.exception("Failed to fetch Evotor revenue")
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=(
                    "Не смог получить выручку из Эвотора. "
                    "Проверьте токен, кассу и настройки приема чеков Evotor."
                ),
                sheet_error=str(exc),
            )

        if evotor_revenue is None:
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error="Интеграция с Эвотором пока выключена в настройках.",
            )

        try:
            scheduled_employee = await self.sheet_sync.scheduled_employee_for_date(today)
        except Exception as exc:
            logger.exception("Failed to read scheduled employee for Evotor import")
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=(
                    "Выручку из Эвотора получил, но не смог прочитать продавца "
                    "из расписания в Google Sheets."
                ),
                sheet_error=str(exc),
            )

        if not scheduled_employee:
            return ProcessedFinanceResult(
                entry=None,
                duplicate=False,
                parse_error=(
                    f"Выручку из Эвотора получил, но на {today:%d.%m.%Y} "
                    "в расписании нет продавца."
                ),
            )

        parsed = ParsedFinanceMessage(
            employee_name=normalize_employee_group(scheduled_employee),
            entry_date=today,
            cash=evotor_revenue.cash,
            cashless=evotor_revenue.cashless,
            raw_text=f"evotor:{today.isoformat()}",
        )

        async def operation() -> ProcessedFinanceResult:
            async with session_scope(self.session_factory) as session:
                employees = EmployeeRepository(session)
                finance_entries = FinanceRepository(session)
                existing_entries = await finance_entries.entries_between(today, today)
                is_no_work_day = any(
                    entry.salary == 0 and looks_like_no_work(entry.raw_text)
                    for entry in existing_entries
                )
                salary = (
                    0
                    if is_no_work_day
                    else await self._salary_for_employee_group(employees, parsed.employee_name)
                )
                employee = await employees.get_or_create(parsed.employee_name, salary)
                stored = await finance_entries.store_entry_once(
                    parsed,
                    employee,
                    salary,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return ProcessedFinanceResult(
                    entry=stored.entry,
                    duplicate=stored.duplicate,
                    parse_error=stored.public_error,
                    updated=stored.status == "updated",
                )

        result = await retry_db(operation)
        if result.entry is None or result.duplicate:
            return result

        try:
            await self.sheet_sync.push_entry(result.entry)
        except Exception as exc:
            logger.exception(
                "Failed to sync Evotor entry to Google Sheets",
                extra={"entry_id": result.entry.id},
            )
            return ProcessedFinanceResult(
                entry=result.entry,
                duplicate=False,
                sheet_error=str(exc),
                updated=result.updated,
            )

        return result

    async def weekly_salary_breakdown(self, today: date) -> tuple[Period, dict[str, int]]:
        period = current_week(today)
        try:
            sheet_totals = await self.sheet_sync.weekly_salary_breakdown(period.start, period.end)
        except Exception:
            logger.exception(
                "Failed to calculate weekly salary from Google Sheets",
                extra={
                    "period_start": period.start.isoformat(),
                    "period_end": period.end.isoformat(),
                },
            )
            sheet_totals = None
        if sheet_totals is not None:
            return period, sheet_totals

        async def operation() -> dict[str, int]:
            async with session_scope(self.session_factory) as session:
                entries = await FinanceRepository(session).entries_between(period.start, period.end)
                employees = await EmployeeRepository(session).list_all()
                salary_by_name = {
                    employee.name.casefold(): employee.salary_amount for employee in employees
                }
                return self._salary_breakdown(entries, salary_by_name)

        return period, await retry_db(operation)

    async def list_employees(self) -> list[tuple[str, int]]:
        async def operation() -> list[tuple[str, int]]:
            async with session_scope(self.session_factory) as session:
                employees = await EmployeeRepository(session).list_all()
                return [(employee.name, employee.salary_amount) for employee in employees]

        return await retry_db(operation)

    async def set_salary(self, name: str, salary_amount: int) -> tuple[str, int]:
        async def operation() -> tuple[str, int]:
            async with session_scope(self.session_factory) as session:
                employee = await EmployeeRepository(session).upsert(name, salary_amount)
                return employee.name, employee.salary_amount

        return await retry_db(operation)

    async def _salary_for_employee_group(
        self,
        employees: EmployeeRepository,
        employee_name: str,
    ) -> int:
        total = 0
        for name in split_employee_group(employee_name):
            if name == OTHER_EMPLOYEE_BUCKET:
                total += self.settings.default_salary
                continue
            employee = await employees.get_or_create(name, self.settings.default_salary)
            total += self.salary_engine.calculate(employee)
        return total

    def _salary_breakdown(
        self,
        entries: list[FinanceEntry],
        salary_by_name: dict[str, int],
    ) -> dict[str, int]:
        totals = {"КСЮША": 0, "НАСТЯ": 0, "КРИСТИНА": 0, OTHER_EMPLOYEE_BUCKET: 0}
        for entry in entries:
            if entry.salary == 0:
                continue
            parts = split_employee_group(entry.employee_name)
            if len(parts) <= 1:
                bucket = employee_bucket(parts[0] if parts else entry.employee_name)
                totals[bucket] += entry.salary
                continue

            planned = [
                self._configured_salary_for_name(name, salary_by_name)
                for name in parts
            ]
            planned_total = sum(planned)
            if planned_total == entry.salary:
                for name, salary in zip(parts, planned, strict=True):
                    totals[employee_bucket(name)] += salary
                continue
            if entry.salary < planned_total:
                totals[employee_bucket(parts[0])] += entry.salary
                continue
            if planned_total <= 0:
                totals[OTHER_EMPLOYEE_BUCKET] += entry.salary
                continue

            remaining = entry.salary
            for index, name in enumerate(parts):
                if index == len(parts) - 1:
                    salary = remaining
                else:
                    salary = round(entry.salary * planned[index] / planned_total)
                    remaining -= salary
                totals[employee_bucket(name)] += salary
        return totals

    def _configured_salary_for_name(self, name: str, salary_by_name: dict[str, int]) -> int:
        if name == OTHER_EMPLOYEE_BUCKET:
            return self.settings.default_salary
        return salary_by_name.get(name.casefold(), self.settings.default_salary)
