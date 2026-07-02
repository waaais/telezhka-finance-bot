from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.parser.models import ParsedFinanceCorrection, ParsedFinanceMessage
from app.storage.models import Employee, FinanceEntry, ProcessedMessage, ReminderChat


@dataclass(frozen=True)
class StoreResult:
    entry: FinanceEntry | None
    duplicate: bool
    status: str
    public_error: str | None = None


class EmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> Employee | None:
        normalized_name = name.strip().casefold()
        result = await self.session.execute(select(Employee))
        for employee in result.scalars().all():
            if employee.name.casefold() == normalized_name:
                return employee
        return None

    async def list_all(self) -> list[Employee]:
        result = await self.session.execute(select(Employee).order_by(Employee.name))
        return list(result.scalars().all())

    async def upsert(self, name: str, salary_amount: int) -> Employee:
        existing = await self.get_by_name(name)
        if existing:
            existing.salary_amount = salary_amount
            await self.session.flush()
            return existing

        employee = Employee(name=name.strip(), salary_amount=salary_amount)
        self.session.add(employee)
        await self.session.flush()
        return employee

    async def get_or_create(self, name: str, default_salary: int) -> Employee:
        existing = await self.get_by_name(name)
        if existing:
            return existing
        return await self.upsert(name=name.strip(), salary_amount=default_salary)


class FinanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def store_entry_once(
        self,
        parsed: ParsedFinanceMessage,
        employee: Employee,
        salary: int,
        *,
        chat_id: int,
        message_id: int,
    ) -> StoreResult:
        processed = ProcessedMessage(
            chat_id=chat_id,
            message_id=message_id,
            status="received",
            raw_text=parsed.raw_text,
        )
        self.session.add(processed)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return StoreResult(entry=None, duplicate=True, status="duplicate")

        existing = await self._find_update_target(parsed.entry_date, parsed.employee_name)
        if existing == "ambiguous":
            processed.status = "needs_clarification"
            await self.session.flush()
            return StoreResult(
                entry=None,
                duplicate=False,
                status="needs_clarification",
                public_error=(
                    f"На {parsed.entry_date:%d.%m.%Y} уже несколько записей. "
                    "Укажите точное имя сотрудника."
                ),
            )

        if existing:
            entry = existing
            entry.employee_id = employee.id
            entry.employee_name = employee.name
            entry.cash = parsed.cash
            entry.cashless = parsed.cashless
            entry.revenue = parsed.revenue
            entry.salary = salary
            entry.source_chat_id = chat_id
            entry.source_message_id = message_id
            entry.raw_text = parsed.raw_text
            status = "updated"
        else:
            entry = FinanceEntry(
                entry_date=parsed.entry_date,
                employee_id=employee.id,
                employee_name=employee.name,
                cash=parsed.cash,
                cashless=parsed.cashless,
                revenue=parsed.revenue,
                salary=salary,
                source_chat_id=chat_id,
                source_message_id=message_id,
                raw_text=parsed.raw_text,
            )
            self.session.add(entry)
            status = "stored"
        await self.session.flush()

        processed.status = status
        processed.entry_id = entry.id
        await self.session.flush()
        return StoreResult(entry=entry, duplicate=False, status=status)

    async def update_entry_once(
        self,
        correction: ParsedFinanceCorrection,
        *,
        chat_id: int,
        message_id: int,
    ) -> StoreResult:
        processed = ProcessedMessage(
            chat_id=chat_id,
            message_id=message_id,
            status="received",
            raw_text=correction.raw_text,
        )
        self.session.add(processed)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return StoreResult(entry=None, duplicate=True, status="duplicate")

        target = await self._find_correction_target(correction)
        if target is None:
            processed.status = "not_found"
            await self.session.flush()
            return StoreResult(
                entry=None,
                duplicate=False,
                status="not_found",
                public_error=(
                    f"Не нашел запись за {correction.entry_date:%d.%m.%Y}. "
                    "Можно прислать день целиком: `2 июля Ксюша нал 19000 безнал 42000`."
                ),
            )
        if target == "ambiguous":
            processed.status = "needs_clarification"
            await self.session.flush()
            return StoreResult(
                entry=None,
                duplicate=False,
                status="needs_clarification",
                public_error=(
                    f"На {correction.entry_date:%d.%m.%Y} несколько записей. "
                    "Напишите имя: `измени Ксюша наличку за 2 июля на 19000`."
                ),
            )

        entry = target
        if correction.cash is not None:
            entry.cash = correction.cash
        if correction.cashless is not None:
            entry.cashless = correction.cashless
        entry.revenue = entry.cash + entry.cashless
        entry.source_chat_id = chat_id
        entry.source_message_id = message_id
        entry.raw_text = correction.raw_text
        await self.session.flush()

        processed.status = "updated"
        processed.entry_id = entry.id
        await self.session.flush()
        return StoreResult(entry=entry, duplicate=False, status="updated")

    async def remember_failed_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        raw_text: str,
        error_text: str,
    ) -> StoreResult:
        processed = ProcessedMessage(
            chat_id=chat_id,
            message_id=message_id,
            status="failed_parse",
            raw_text=raw_text,
            error_text=error_text,
        )
        self.session.add(processed)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return StoreResult(entry=None, duplicate=True, status="duplicate")
        return StoreResult(entry=None, duplicate=False, status="failed_parse")

    async def aggregate(self, start_date: date, end_date: date) -> dict[str, int]:
        stmt = (
            select(
                func.coalesce(func.sum(FinanceEntry.cash), 0),
                func.coalesce(func.sum(FinanceEntry.cashless), 0),
                func.coalesce(func.sum(FinanceEntry.revenue), 0),
                func.coalesce(func.sum(FinanceEntry.salary), 0),
                func.count(FinanceEntry.id),
            )
            .where(FinanceEntry.entry_date >= start_date)
            .where(FinanceEntry.entry_date <= end_date)
        )
        row = (await self.session.execute(stmt)).one()
        revenue = int(row[2])
        salaries = int(row[3])
        return {
            "cash": int(row[0]),
            "cashless": int(row[1]),
            "revenue": revenue,
            "salaries": salaries,
            "profit": revenue - salaries,
            "entries": int(row[4]),
        }

    async def recent_entries(self, limit: int = 20) -> list[FinanceEntry]:
        stmt: Select[tuple[FinanceEntry]] = (
            select(FinanceEntry).order_by(FinanceEntry.id.desc()).limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _find_update_target(
        self,
        entry_date: date,
        employee_name: str,
    ) -> FinanceEntry | str | None:
        entries = await self._entries_for_date(entry_date)
        matching = [
            entry for entry in entries if entry.employee_name.casefold() == employee_name.casefold()
        ]
        if matching:
            return matching[0]
        if len(entries) == 1:
            return entries[0]
        if len(entries) > 1:
            return "ambiguous"
        return None

    async def _find_correction_target(
        self,
        correction: ParsedFinanceCorrection,
    ) -> FinanceEntry | str | None:
        entries = await self._entries_for_date(correction.entry_date)
        if correction.employee_name:
            matching = [
                entry
                for entry in entries
                if entry.employee_name.casefold() == correction.employee_name.casefold()
            ]
            return matching[0] if matching else None
        if len(entries) == 1:
            return entries[0]
        if len(entries) > 1:
            return "ambiguous"
        return None

    async def _entries_for_date(self, entry_date: date) -> list[FinanceEntry]:
        stmt: Select[tuple[FinanceEntry]] = (
            select(FinanceEntry)
            .where(FinanceEntry.entry_date == entry_date)
            .order_by(FinanceEntry.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ReminderChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enable(self, chat_id: int) -> ReminderChat:
        result = await self.session.execute(
            select(ReminderChat).where(ReminderChat.chat_id == chat_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.enabled = True
            await self.session.flush()
            return existing

        chat = ReminderChat(chat_id=chat_id, enabled=True)
        self.session.add(chat)
        await self.session.flush()
        return chat

    async def enabled_chat_ids(self) -> list[int]:
        result = await self.session.execute(
            select(ReminderChat.chat_id)
            .where(ReminderChat.enabled.is_(True))
            .order_by(ReminderChat.chat_id)
        )
        return [int(chat_id) for chat_id in result.scalars().all()]

    async def seed_from_processed_messages(self) -> None:
        result = await self.session.execute(select(ProcessedMessage.chat_id).distinct())
        for chat_id in result.scalars().all():
            await self.enable(int(chat_id))


def day_bounds(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, time.min), datetime.combine(day, time.max)
