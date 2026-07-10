from datetime import date
from unittest import TestCase

from app.parser.finance_parser import (
    looks_like_correction,
    looks_like_no_work,
    looks_like_schedule,
    parse_finance_correction,
    parse_finance_message,
    parse_no_work_message,
    parse_schedule_message,
)
from app.parser.models import ParseError


class ParserTest(TestCase):
    def test_parse_multiline_default_date(self) -> None:
        parsed = parse_finance_message(
            "Ксюша\nнал 12500\nбезнал 38640",
            now=date(2026, 7, 2),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша")
        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertEqual(parsed.cash, 12500)
        self.assertEqual(parsed.cashless, 38640)
        self.assertEqual(parsed.revenue, 51140)

    def test_parse_default_date_is_today(self) -> None:
        parsed = parse_finance_message(
            "Настя нал 1000 безнал 2000",
            now=date(2026, 8, 1),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 8, 1))

    def test_parse_russian_date_inline(self) -> None:
        parsed = parse_finance_message(
            "2 июля Настя нал 14000 безнал 42000",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Настя")
        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertEqual(parsed.cash, 14000)
        self.assertEqual(parsed.cashless, 42000)

    def test_parse_lowercase_name_and_spaced_numbers(self) -> None:
        parsed = parse_finance_message(
            "сегодня ксюша наличка 10 000 карта 22 500",
            now=date(2026, 7, 2),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша")
        self.assertEqual(parsed.cash, 10000)
        self.assertEqual(parsed.cashless, 22500)

    def test_parse_revenue_for_previous_weekday(self) -> None:
        parsed = parse_finance_message(
            "выручка за пятницу Ксюша нал 13000 безнал 28000",
            now=date(2026, 7, 6),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша")
        self.assertEqual(parsed.entry_date, date(2026, 7, 3))
        self.assertEqual(parsed.cash, 13000)
        self.assertEqual(parsed.cashless, 28000)

    def test_parse_revenue_for_abbreviated_weekday(self) -> None:
        parsed = parse_finance_message(
            "выручка за пт. Настя нал 13000 безнал 28000",
            now=date(2026, 7, 6),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Настя")
        self.assertEqual(parsed.entry_date, date(2026, 7, 3))

    def test_parse_revenue_for_numeric_date(self) -> None:
        parsed = parse_finance_message(
            "выручка за 03.07 Ксюша нал 13000 безнал 28000",
            now=date(2026, 7, 6),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша")
        self.assertEqual(parsed.entry_date, date(2026, 7, 3))

    def test_parse_today_typo_and_missing_employee_when_allowed(self) -> None:
        parsed = parse_finance_message(
            "сегорлня нал 13000 безнал 28000",
            now=date(2026, 7, 6),
            timezone="Europe/Moscow",
            allow_missing_employee=True,
        )

        self.assertEqual(parsed.employee_name, "")
        self.assertEqual(parsed.entry_date, date(2026, 7, 6))
        self.assertEqual(parsed.cash, 13000)
        self.assertEqual(parsed.cashless, 28000)

    def test_parse_composite_employee_name(self) -> None:
        parsed = parse_finance_message(
            "ксюша+дима нал 500 безнал 1000",
            now=date(2026, 7, 2),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша+Дима")
        self.assertEqual(parsed.cash, 500)
        self.assertEqual(parsed.cashless, 1000)

    def test_parse_schedule_message(self) -> None:
        text = (
            "пн. 29.06 — Ксюша\n"
            "вт. 30.06 — Настя\n"
            "пт. 03.07 — Ксюша + Дима\n"
            "вс. 12.07 — Ксюша + &"
        )

        self.assertTrue(looks_like_schedule(text))
        parsed = parse_schedule_message(text, now=date(2026, 7, 4))

        self.assertEqual(len(parsed.entries), 4)
        self.assertEqual(parsed.entries[0].entry_date, date(2026, 6, 29))
        self.assertEqual(parsed.entries[0].employee_name, "Ксюша")
        self.assertEqual(parsed.entries[2].employee_name, "Ксюша+Дима")
        self.assertEqual(parsed.entries[3].employee_name, "Ксюша+&")

    def test_parse_schedule_ignores_title_and_treats_question_as_other(self) -> None:
        text = (
            "РАСПИСАНИЕ — ИЮЛЬ 1/2\n"
            "пн. 06.07 — Кристина\n"
            "вт. 07.07 — Настя\n"
            "вс. 12.07 — Ксюша + ?"
        )

        parsed = parse_schedule_message(text, now=date(2026, 7, 4))

        self.assertEqual(len(parsed.entries), 3)
        self.assertEqual(parsed.entries[0].employee_name, "Кристина")
        self.assertEqual(parsed.entries[2].employee_name, "Ксюша+&")

    def test_one_schedule_line_is_schedule(self) -> None:
        self.assertTrue(looks_like_schedule("пн. 29.06 — Ксюша"))

    def test_parse_no_work_message(self) -> None:
        self.assertTrue(looks_like_no_work("сегодня не работаем"))

        parsed = parse_no_work_message("сегодня не работаем", now=date(2026, 7, 4))

        self.assertEqual(parsed.entry_date, date(2026, 7, 4))

    def test_parse_no_work_tomorrow(self) -> None:
        parsed = parse_no_work_message("завтра не работаем", now=date(2026, 7, 10))

        self.assertEqual(parsed.entry_date, date(2026, 7, 11))

    def test_parse_no_work_after_tomorrow(self) -> None:
        parsed = parse_no_work_message("послезавтра не работаем", now=date(2026, 7, 10))

        self.assertEqual(parsed.entry_date, date(2026, 7, 12))

    def test_parse_no_work_numeric_future_date(self) -> None:
        parsed = parse_no_work_message("12.08 не работаем", now=date(2026, 7, 10))

        self.assertEqual(parsed.entry_date, date(2026, 8, 12))

    def test_parse_error_is_public_and_helpful(self) -> None:
        with self.assertRaises(ParseError) as error:
            parse_finance_message("Настя нал 1000", now=date(2026, 7, 2), timezone="Europe/Moscow")

        self.assertIn("безнал", error.exception.public_message)

    def test_dot_thousand_separator_is_not_treated_as_date(self) -> None:
        parsed = parse_finance_message(
            "Ксюша нал 10.000 безнал 20.000",
            now=date(2026, 7, 2),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertEqual(parsed.cash, 10000)
        self.assertEqual(parsed.cashless, 20000)

    def test_parse_cash_correction_without_employee(self) -> None:
        self.assertTrue(looks_like_correction("измени наличку за 2 июля на 19000"))

        parsed = parse_finance_correction(
            "измени наличку за 2 июля на 19000",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertIsNone(parsed.employee_name)
        self.assertEqual(parsed.cash, 19000)
        self.assertIsNone(parsed.cashless)

    def test_parse_correction_default_date_is_today(self) -> None:
        parsed = parse_finance_correction(
            "измени наличку на 19000",
            now=date(2026, 8, 1),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 8, 1))
        self.assertEqual(parsed.cash, 19000)

    def test_parse_cashless_correction_with_employee(self) -> None:
        parsed = parse_finance_correction(
            "исправь Ксюша безнал за 02.07 на 42000",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertEqual(parsed.employee_name, "Ксюша")
        self.assertIsNone(parsed.cash)
        self.assertEqual(parsed.cashless, 42000)

    def test_parse_correction_with_composite_employee(self) -> None:
        parsed = parse_finance_correction(
            "исправь Ксюша+Дима безнал за 02.07 на 42000",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.employee_name, "Ксюша+Дима")
        self.assertIsNone(parsed.new_employee_name)

    def test_parse_employee_correction(self) -> None:
        parsed = parse_finance_correction(
            "измени продавца за 3 июля на Дима",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 7, 3))
        self.assertIsNone(parsed.employee_name)
        self.assertEqual(parsed.new_employee_name, "Дима")
        self.assertIsNone(parsed.cash)
        self.assertIsNone(parsed.cashless)

    def test_parse_employee_correction_with_typo(self) -> None:
        parsed = parse_finance_correction(
            "измени продаца 02.07 на Дима",
            now=date(2026, 7, 10),
            timezone="Europe/Moscow",
        )

        self.assertEqual(parsed.entry_date, date(2026, 7, 2))
        self.assertEqual(parsed.new_employee_name, "Дима")
