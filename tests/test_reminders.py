from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from app.reminders import _next_monthly_at, _next_reminder_at, _next_weekly_at


class ReminderScheduleTest(TestCase):
    def test_next_reminder_today_before_target_time(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_reminder_at(
            datetime(2026, 7, 2, 15, 0, tzinfo=timezone),
            (22, 30),
        )

        self.assertEqual(next_run, datetime(2026, 7, 2, 22, 30, tzinfo=timezone))

    def test_next_reminder_tomorrow_after_target_time(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_reminder_at(
            datetime(2026, 7, 2, 23, 0, tzinfo=timezone),
            (22, 30),
        )

        self.assertEqual(next_run, datetime(2026, 7, 3, 22, 30, tzinfo=timezone))

    def test_next_weekly_report_this_sunday_before_target_time(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_weekly_at(
            datetime(2026, 7, 5, 22, 0, tzinfo=timezone),
            weekday=6,
            hour_minute=(23, 0),
        )

        self.assertEqual(next_run, datetime(2026, 7, 5, 23, 0, tzinfo=timezone))

    def test_next_weekly_report_next_sunday_after_target_time(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_weekly_at(
            datetime(2026, 7, 5, 23, 1, tzinfo=timezone),
            weekday=6,
            hour_minute=(23, 0),
        )

        self.assertEqual(next_run, datetime(2026, 7, 12, 23, 0, tzinfo=timezone))

    def test_next_monthly_payment_reminder_this_month(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_monthly_at(
            datetime(2026, 7, 1, 9, 0, tzinfo=timezone),
            day=1,
            hour_minute=(10, 0),
        )

        self.assertEqual(next_run, datetime(2026, 7, 1, 10, 0, tzinfo=timezone))

    def test_next_monthly_payment_reminder_next_month(self) -> None:
        timezone = ZoneInfo("Europe/Moscow")

        next_run = _next_monthly_at(
            datetime(2026, 7, 1, 10, 1, tzinfo=timezone),
            day=1,
            hour_minute=(10, 0),
        )

        self.assertEqual(next_run, datetime(2026, 8, 1, 10, 0, tzinfo=timezone))
