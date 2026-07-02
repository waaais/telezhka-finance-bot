from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from app.reminders import _next_reminder_at


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
