from datetime import date
from unittest import TestCase

from app.statistics.engine import current_month, current_week


class StatisticsTest(TestCase):
    def test_current_week_starts_on_monday(self) -> None:
        period = current_week(date(2026, 7, 2))

        self.assertEqual(period.start, date(2026, 6, 29))
        self.assertEqual(period.end, date(2026, 7, 2))

    def test_current_month(self) -> None:
        period = current_month(date(2026, 7, 2))

        self.assertEqual(period.start, date(2026, 7, 1))
        self.assertEqual(period.end, date(2026, 7, 2))
