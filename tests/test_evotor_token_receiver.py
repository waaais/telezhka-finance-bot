import json
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.integrations.evotor_token_receiver import (
    extract_token,
    load_receipts,
    load_token,
    save_receipt,
    save_token,
)


class EvotorTokenReceiverTest(TestCase):
    def test_extract_token_from_nested_payload(self) -> None:
        token = extract_token(
            {
                "event": "install",
                "data": {
                    "appToken": "evotor-app-token",
                },
            }
        )

        self.assertEqual(token, "evotor-app-token")

    def test_save_and_load_token(self) -> None:
        with TemporaryDirectory() as tmpdir:
            token_file = f"{tmpdir}/evotor-token.json"

            save_token(token_file, "secret-token")

            self.assertEqual(load_token(token_file), "secret-token")
            with open(token_file, encoding="utf-8") as file:
                payload = json.load(file)
            self.assertIn("received_at", payload)

    def test_missing_token_file_returns_empty_string(self) -> None:
        with TemporaryDirectory() as tmpdir:
            self.assertEqual(load_token(f"{tmpdir}/missing.json"), "")

    def test_save_receipt_redacts_secret(self) -> None:
        with TemporaryDirectory() as tmpdir:
            receipts_file = f"{tmpdir}/evotor-receipts.jsonl"

            save_receipt(
                receipts_file,
                {
                    "secret": "callback-secret",
                    "deviceId": "terminal-1",
                    "totalAmount": 1200,
                },
            )

            receipts = load_receipts(receipts_file)
            self.assertEqual(len(receipts), 1)
            self.assertIn("received_at", receipts[0])
            self.assertNotIn("secret", receipts[0]["payload"])
            self.assertEqual(receipts[0]["payload"]["totalAmount"], 1200)
