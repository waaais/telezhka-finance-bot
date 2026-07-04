from unittest import TestCase

from app.integrations.evotor import extract_revenue


class EvotorExtractRevenueTest(TestCase):
    def test_extract_explicit_totals(self) -> None:
        cash, cashless = extract_revenue({"cash": 1000, "cashless": 2500})

        self.assertEqual(cash, 1000)
        self.assertEqual(cashless, 2500)

    def test_extract_receipts_with_payments(self) -> None:
        cash, cashless = extract_revenue(
            {
                "items": [
                    {
                        "terminalUuid": "terminal-1",
                        "payments": [
                            {"type": "CASH", "amount": 700},
                            {"type": "CARD", "amount": 1300},
                        ],
                    },
                    {
                        "terminalUuid": "terminal-2",
                        "payments": [{"type": "CARD", "amount": 9999}],
                    },
                ]
            },
            terminal_uuid="terminal-1",
        )

        self.assertEqual(cash, 700)
        self.assertEqual(cashless, 1300)

    def test_unknown_payment_defaults_to_cashless(self) -> None:
        cash, cashless = extract_revenue({"receipts": [{"payments": [{"amount": 500}]}]})

        self.assertEqual(cash, 0)
        self.assertEqual(cashless, 500)
