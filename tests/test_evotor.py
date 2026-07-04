import asyncio
from datetime import date
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from app.integrations.evotor import EvotorReceiptFileSync, extract_revenue
from app.integrations.evotor_token_receiver import save_receipt


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

    def test_extract_evotor_receipt_fields(self) -> None:
        cash, cashless = extract_revenue(
            {
                "deviceId": "00307900861869",
                "dateTime": "2026-07-04T17:46:00+03:00",
                "totalAmount": 1200,
                "paymentSource": "CARD",
                "type": "SELL",
            },
            terminal_uuid="00307900861869",
        )

        self.assertEqual(cash, 0)
        self.assertEqual(cashless, 1200)

    def test_return_receipt_subtracts_revenue(self) -> None:
        cash, cashless = extract_revenue(
            {
                "receipts": [
                    {"deviceId": "terminal-1", "totalAmount": 3000, "paymentSource": "CASH", "type": "SELL"},
                    {"deviceId": "terminal-1", "totalAmount": 500, "paymentSource": "CASH", "type": "PAYBACK"},
                ]
            },
            terminal_uuid="terminal-1",
        )

        self.assertEqual(cash, 2500)
        self.assertEqual(cashless, 0)

    def test_receipt_file_sync_filters_date_and_terminal(self) -> None:
        with TemporaryDirectory() as tmpdir:
            receipts_file = f"{tmpdir}/evotor-receipts.jsonl"
            save_receipt(
                receipts_file,
                {
                    "deviceId": "terminal-1",
                    "dateTime": "2026-07-04T10:00:00+03:00",
                    "totalAmount": 1500,
                    "paymentSource": "CASH",
                    "type": "SELL",
                },
            )
            save_receipt(
                receipts_file,
                {
                    "deviceId": "terminal-2",
                    "dateTime": "2026-07-04T10:00:00+03:00",
                    "totalAmount": 9999,
                    "paymentSource": "CARD",
                    "type": "SELL",
                },
            )
            save_receipt(
                receipts_file,
                {
                    "deviceId": "terminal-1",
                    "dateTime": "2026-07-03T10:00:00+03:00",
                    "totalAmount": 8888,
                    "paymentSource": "CARD",
                    "type": "SELL",
                },
            )
            sync = EvotorReceiptFileSync(
                SimpleNamespace(
                    evotor_receipts_file=receipts_file,
                    evotor_terminal_uuid="terminal-1",
                )
            )

            revenue = asyncio.run(sync.fetch_revenue(date(2026, 7, 4)))

            self.assertIsNotNone(revenue)
            assert revenue is not None
            self.assertEqual(revenue.cash, 1500)
            self.assertEqual(revenue.cashless, 0)
