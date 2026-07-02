import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.integrations.google_sheets import SheetSync
from app.parser.finance_parser import (
    looks_like_correction,
    parse_finance_correction,
    parse_finance_message,
)
from app.parser.models import ParseError
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


class FinanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        salary_engine: SalaryEngine,
        sheet_sync: SheetSync,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.salary_engine = salary_engine
        self.sheet_sync = sheet_sync

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
        if looks_like_correction(text):
            return await self._process_correction_message(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                today=today,
            )

        try:
            parsed = parse_finance_message(text, now=today, timezone=self.settings.timezone)
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
                employees = EmployeeRepository(session)
                employee = await employees.get_or_create(
                    parsed.employee_name,
                    self.settings.default_salary,
                )
                salary = self.salary_engine.calculate(employee)
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
                stored = await FinanceRepository(session).update_entry_once(
                    correction,
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
        async def operation() -> dict[str, int]:
            async with session_scope(self.session_factory) as session:
                return await FinanceRepository(session).aggregate(period.start, period.end)

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
