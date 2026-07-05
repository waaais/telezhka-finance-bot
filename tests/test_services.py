from datetime import date
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.config import Settings
from app.integrations.evotor import EvotorRevenue
from app.salary.engine import SalaryEngine
from app.services import FinanceService
from app.storage.database import create_engine, create_session_factory, init_db, session_scope
from app.storage.repositories import FinanceRepository


class FakeSheetSync:
    def __init__(self, scheduled_employee: str | None) -> None:
        self.scheduled_employee = scheduled_employee
        self.pushed_entries = []

    async def push_entry(self, entry) -> None:
        self.pushed_entries.append(
            SimpleNamespace(
                entry_date=entry.entry_date,
                employee_name=entry.employee_name,
                cash=entry.cash,
                cashless=entry.cashless,
                salary=entry.salary,
            )
        )

    async def push_schedule(self, entries) -> None:
        return None

    async def scheduled_employee_for_date(self, entry_date: date) -> str | None:
        return self.scheduled_employee

    async def aggregate_period(self, start_date: date, end_date: date):
        return None

    async def weekly_salary_breakdown(self, start_date: date, end_date: date):
        return None


class FakeEvotorSync:
    def __init__(self, cash: int, cashless: int) -> None:
        self.cash = cash
        self.cashless = cashless

    async def fetch_revenue(self, entry_date: date):
        return EvotorRevenue(
            entry_date=entry_date,
            cash=self.cash,
            cashless=self.cashless,
            raw={},
        )


class FinanceServiceNoWorkTest(IsolatedAsyncioTestCase):
    async def test_manual_revenue_without_employee_uses_schedule(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = Settings(
                DATABASE_URL=f"sqlite+aiosqlite:///{tmpdir}/finance.db",
                GOOGLE_SHEETS_ENABLED=False,
            )
            engine = create_engine(settings)
            await init_db(engine)
            session_factory = create_session_factory(engine)
            sheet_sync = FakeSheetSync("Кристина+Ксюша")
            service = FinanceService(
                session_factory=session_factory,
                settings=settings,
                salary_engine=SalaryEngine(),
                sheet_sync=sheet_sync,
                evotor_sync=FakeEvotorSync(cash=0, cashless=0),
            )
            await service.seed_default_employees()

            result = await service.process_text_message(
                text="сегорлня нал 13000 безнал 28000",
                chat_id=1,
                message_id=1,
                today=date(2026, 7, 5),
            )

            self.assertIsNotNone(result.entry)
            self.assertEqual(result.entry.employee_name, "Кристина+Ксюша")
            self.assertEqual(result.entry.cash, 13000)
            self.assertEqual(result.entry.cashless, 28000)
            self.assertEqual(result.entry.salary, 4000)
            self.assertEqual(sheet_sync.pushed_entries[-1].employee_name, "Кристина+Ксюша")

            await engine.dispose()

    async def test_manual_revenue_updates_evotor_zero_entry_for_same_scheduled_employee(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = Settings(
                DATABASE_URL=f"sqlite+aiosqlite:///{tmpdir}/finance.db",
                GOOGLE_SHEETS_ENABLED=False,
            )
            engine = create_engine(settings)
            await init_db(engine)
            session_factory = create_session_factory(engine)
            sheet_sync = FakeSheetSync("Кристина+Ксюша")
            service = FinanceService(
                session_factory=session_factory,
                settings=settings,
                salary_engine=SalaryEngine(),
                sheet_sync=sheet_sync,
                evotor_sync=FakeEvotorSync(cash=0, cashless=0),
            )
            await service.seed_default_employees()

            evotor_result = await service.import_evotor_revenue(
                chat_id=1,
                message_id=1,
                today=date(2026, 7, 5),
            )
            self.assertIsNotNone(evotor_result.entry)
            self.assertEqual(evotor_result.entry.cash, 0)
            self.assertEqual(evotor_result.entry.cashless, 0)

            manual_result = await service.process_text_message(
                text="сегодня нал 13000 безнал 28000",
                chat_id=1,
                message_id=2,
                today=date(2026, 7, 5),
            )

            self.assertIsNotNone(manual_result.entry)
            self.assertTrue(manual_result.updated)
            self.assertEqual(manual_result.entry.employee_name, "Кристина+Ксюша")
            self.assertEqual(manual_result.entry.cash, 13000)
            self.assertEqual(manual_result.entry.cashless, 28000)
            self.assertEqual(manual_result.entry.revenue, 41000)

            async with session_scope(session_factory) as session:
                entries = await FinanceRepository(session).entries_between(
                    date(2026, 7, 5),
                    date(2026, 7, 5),
                )
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].cash, 13000)
            self.assertEqual(entries[0].cashless, 28000)

            await engine.dispose()

    async def test_manual_revenue_without_employee_requires_schedule(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = Settings(
                DATABASE_URL=f"sqlite+aiosqlite:///{tmpdir}/finance.db",
                GOOGLE_SHEETS_ENABLED=False,
            )
            engine = create_engine(settings)
            await init_db(engine)
            session_factory = create_session_factory(engine)
            service = FinanceService(
                session_factory=session_factory,
                settings=settings,
                salary_engine=SalaryEngine(),
                sheet_sync=FakeSheetSync(None),
                evotor_sync=FakeEvotorSync(cash=0, cashless=0),
            )

            result = await service.process_text_message(
                text="сегодня нал 13000 безнал 28000",
                chat_id=1,
                message_id=1,
                today=date(2026, 7, 5),
            )

            self.assertIsNotNone(result.parse_error)
            self.assertIn("в расписании нет сотрудника", result.parse_error)

            await engine.dispose()

    async def test_evotor_does_not_restore_salary_for_no_work_day(self) -> None:
        with TemporaryDirectory() as tmpdir:
            settings = Settings(
                DATABASE_URL=f"sqlite+aiosqlite:///{tmpdir}/finance.db",
                GOOGLE_SHEETS_ENABLED=False,
            )
            engine = create_engine(settings)
            await init_db(engine)
            session_factory = create_session_factory(engine)
            sheet_sync = FakeSheetSync("Кристина+Ксюша")
            service = FinanceService(
                session_factory=session_factory,
                settings=settings,
                salary_engine=SalaryEngine(),
                sheet_sync=sheet_sync,
                evotor_sync=FakeEvotorSync(cash=0, cashless=0),
            )
            await service.seed_default_employees()

            no_work_result = await service.process_text_message(
                text="сегодня не работаем",
                chat_id=1,
                message_id=1,
                today=date(2026, 7, 5),
            )
            self.assertIsNotNone(no_work_result.entry)
            self.assertEqual(no_work_result.entry.salary, 0)

            evotor_result = await service.import_evotor_revenue(
                chat_id=1,
                message_id=2,
                today=date(2026, 7, 5),
            )

            self.assertIsNotNone(evotor_result.entry)
            self.assertEqual(evotor_result.entry.salary, 0)
            self.assertEqual(sheet_sync.pushed_entries[-1].salary, 0)

            async with session_scope(session_factory) as session:
                entries = await FinanceRepository(session).entries_between(
                    date(2026, 7, 5),
                    date(2026, 7, 5),
                )
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].salary, 0)

            await engine.dispose()
