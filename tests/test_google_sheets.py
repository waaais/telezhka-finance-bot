from datetime import date
from unittest import TestCase

from app.integrations.google_sheets import (
    _aggregate_rows_for_dates,
    _date_to_sheet_number,
    _find_weekly_salary_block,
    _sheet_title_for_date,
    _weekly_salary_totals,
    _weekly_salary_totals_for_dates,
)


class GoogleSheetsWeeklySalaryTest(TestCase):
    def test_weekly_totals_group_named_employees_and_others(self) -> None:
        rows = self._sample_rows()
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 4)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 4000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 0)

    def test_weekly_totals_split_composite_employee_names(self) -> None:
        rows = self._sample_rows()
        rows[4][1:3] = ["Ксюша+Дима", 4500]
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 4)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 6000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 2500)

    def test_weekly_totals_keep_partial_composite_salary_on_first_employee(self) -> None:
        rows = self._sample_rows()
        rows[4][1:3] = ["Ксюша+Дима", 2000]
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 4)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 6000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 0)

    def test_weekly_totals_include_rows_from_adjacent_month_sheet(self) -> None:
        june_rows = self._daily_rows(
            [
                (date(2026, 6, 29), "Ксюша", 2000),
                (date(2026, 6, 30), "Настя", 2000),
            ]
        )
        july_rows = self._daily_rows(
            [
                (date(2026, 7, 1), "Кристина", 2000),
                (date(2026, 7, 2), "Дима", 2500),
                (date(2026, 7, 3), "Ксюша+Дима", 4500),
                (date(2026, 7, 4), "Настя+Ксюша", 4000),
                (date(2026, 7, 5), "Кристина+Ксюша", 4000),
            ]
        )

        totals = _weekly_salary_totals_for_dates(
            {
                "ИЮНЬ 2026": june_rows,
                "ИЮЛЬ 2026": july_rows,
            },
            [
                date(2026, 6, 29),
                date(2026, 6, 30),
                date(2026, 7, 1),
                date(2026, 7, 2),
                date(2026, 7, 3),
                date(2026, 7, 4),
                date(2026, 7, 5),
            ],
        )

        self.assertEqual(totals["КСЮША"], 8000)
        self.assertEqual(totals["НАСТЯ"], 4000)
        self.assertEqual(totals["КРИСТИНА"], 4000)
        self.assertEqual(totals["&"], 5000)

    def test_monthly_aggregate_excludes_adjacent_previous_month_rows(self) -> None:
        june_rows = self._daily_rows(
            [
                (date(2026, 6, 29), "Ксюша", 2000, 5000, 10000),
                (date(2026, 6, 30), "Настя", 2000, 3440, 15330),
            ]
        )
        july_rows = self._daily_rows(
            [
                (date(2026, 7, 1), "Настя", 2000, 7110, 18660),
                (date(2026, 7, 2), "Дима", 2500, 3560, 17320),
                (date(2026, 7, 3), "Ксюша+Дима", 4500, 3290, 11680),
                (date(2026, 7, 4), "Настя+Ксюша", 0, 0, 0),
            ]
        )

        totals = _aggregate_rows_for_dates(
            {
                "ИЮНЬ 2026": june_rows,
                "ИЮЛЬ 2026": july_rows,
            },
            [
                date(2026, 7, 1),
                date(2026, 7, 2),
                date(2026, 7, 3),
                date(2026, 7, 4),
            ],
        )

        self.assertEqual(totals["cash"], 13960)
        self.assertEqual(totals["cashless"], 47660)
        self.assertEqual(totals["revenue"], 61620)
        self.assertEqual(totals["salaries"], 9000)
        self.assertEqual(totals["profit"], 52620)
        self.assertEqual(totals["entries"], 4)

    def test_second_block_is_used_for_dates_after_next_salary_header(self) -> None:
        rows = self._sample_rows()
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 8)))

        self.assertIsNotNone(block)
        self.assertEqual(block.label_rows["КСЮША"], 10)
        self.assertEqual(block.label_rows["&"], 13)

    def test_second_block_is_used_from_monday_of_second_calendar_week(self) -> None:
        rows = self._sample_rows()

        monday_block = _find_weekly_salary_block(
            rows,
            _date_to_sheet_number(date(2026, 7, 6)),
        )
        tuesday_block = _find_weekly_salary_block(
            rows,
            _date_to_sheet_number(date(2026, 7, 7)),
        )

        self.assertIsNotNone(monday_block)
        self.assertIsNotNone(tuesday_block)
        self.assertEqual(monday_block.label_rows["КСЮША"], 10)
        self.assertEqual(tuesday_block.label_rows["КСЮША"], 10)
        self.assertEqual(monday_block.data_start_row, 7)
        self.assertEqual(monday_block.data_end_row, 13)

    def test_missing_weekly_block_returns_none(self) -> None:
        rows = self._sample_rows()

        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 8, 1)))

        self.assertIsNone(block)

    def test_sheet_title_follows_entry_month(self) -> None:
        self.assertEqual(_sheet_title_for_date(date(2026, 7, 31)), "ИЮЛЬ 2026")
        self.assertEqual(_sheet_title_for_date(date(2026, 8, 1)), "АВГУСТ 2026")

    def _sample_rows(self) -> list[list[object]]:
        blank = [None] * 8
        rows = [blank.copy() for _ in range(16)]
        for row_number, entry_date in enumerate(
            (
                date(2026, 7, 1),
                date(2026, 7, 2),
                date(2026, 7, 3),
                date(2026, 7, 4),
                date(2026, 7, 5),
                date(2026, 7, 6),
                date(2026, 7, 7),
                date(2026, 7, 8),
                date(2026, 7, 9),
                date(2026, 7, 10),
                date(2026, 7, 11),
                date(2026, 7, 12),
                date(2026, 7, 13),
                date(2026, 7, 14),
                date(2026, 7, 15),
            ),
            start=2,
        ):
            rows[row_number - 1][0] = _date_to_sheet_number(entry_date)

        rows[1][6] = "З/П"
        rows[2][1:3] = ["Ксюша", 2000]
        rows[2][6] = "КСЮША"
        rows[3][1:3] = ["Настя", 2000]
        rows[3][6] = "НАСТЯ"
        rows[4][6] = "КРИСТИНА"
        rows[5][6] = "&"
        rows[5][1:3] = ["Ксюша", 2000]
        rows[6][1:3] = ["Петя", 2500]
        rows[7][1:3] = ["Оля", 2500]
        rows[8][6] = "З/П"
        rows[9][6] = "КСЮША"
        rows[10][6] = "НАСТЯ"
        rows[11][6] = "КРИСТИНА"
        rows[12][6] = "&"
        return rows

    def _daily_rows(
        self,
        entries: list[
            tuple[date, str, int] | tuple[date, str, int, int, int]
        ],
    ) -> list[list[object]]:
        rows: list[list[object]] = []
        for entry in entries:
            entry_date, employee_name, salary = entry[:3]
            cash = entry[3] if len(entry) > 3 else ""
            cashless = entry[4] if len(entry) > 4 else ""
            rows.append([
                _date_to_sheet_number(entry_date),
                employee_name,
                salary,
                cash,
                cashless,
            ])
        return rows
