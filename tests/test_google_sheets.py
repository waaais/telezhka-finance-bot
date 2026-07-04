from datetime import date
from unittest import TestCase

from app.integrations.google_sheets import (
    _date_to_sheet_number,
    _find_weekly_salary_block,
    _sheet_title_for_date,
    _weekly_salary_totals,
)


class GoogleSheetsWeeklySalaryTest(TestCase):
    def test_weekly_totals_group_named_employees_and_others(self) -> None:
        rows = self._sample_rows()
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 7)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 4000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 5000)

    def test_weekly_totals_split_composite_employee_names(self) -> None:
        rows = self._sample_rows()
        rows[4][1:3] = ["Ксюша+Дима", 4500]
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 4)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 6000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 7500)

    def test_weekly_totals_keep_partial_composite_salary_on_first_employee(self) -> None:
        rows = self._sample_rows()
        rows[4][1:3] = ["Ксюша+Дима", 2000]
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 4)))

        self.assertIsNotNone(block)
        totals = _weekly_salary_totals(rows, block)

        self.assertEqual(totals["КСЮША"], 6000)
        self.assertEqual(totals["НАСТЯ"], 2000)
        self.assertEqual(totals["КРИСТИНА"], 0)
        self.assertEqual(totals["&"], 5000)

    def test_second_block_is_used_for_dates_after_next_salary_header(self) -> None:
        rows = self._sample_rows()
        block = _find_weekly_salary_block(rows, _date_to_sheet_number(date(2026, 7, 8)))

        self.assertIsNotNone(block)
        self.assertEqual(block.label_rows["КСЮША"], 10)
        self.assertEqual(block.label_rows["&"], 13)

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
