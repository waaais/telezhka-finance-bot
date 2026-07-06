import json
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.integrations.evotor_token_receiver import (
    _parse_raw_body,
    evotor_status_payload,
    extract_token,
    load_receipts,
    load_token,
    looks_like_receipt_payload,
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
                    "token": "callback-token",
                    "deviceId": "terminal-1",
                    "totalAmount": 1200,
                },
            )

            receipts = load_receipts(receipts_file)
            self.assertEqual(len(receipts), 1)
            self.assertIn("received_at", receipts[0])
            self.assertNotIn("secret", receipts[0]["payload"])
            self.assertNotIn("token", receipts[0]["payload"])
            self.assertEqual(receipts[0]["payload"]["totalAmount"], 1200)

    def test_receipt_payload_is_detected_inside_items(self) -> None:
        self.assertTrue(
            looks_like_receipt_payload(
                {
                    "items": [
                        {
                            "deviceId": "terminal-1",
                            "dateTime": "2026-07-05T12:00:00+03:00",
                            "totalAmount": 1200,
                        }
                    ]
                }
            )
        )

    def test_parse_raw_json_body_with_text_content_type(self) -> None:
        self.assertEqual(
            _parse_raw_body('{"deviceId":"terminal-1","totalAmount":120}'),
            {"deviceId": "terminal-1", "totalAmount": 120},
        )
        self.assertEqual(
            _parse_raw_body('[{"deviceId":"terminal-1"}]'),
            [{"deviceId": "terminal-1"}],
        )
        self.assertIsNone(_parse_raw_body("not json"))

    def test_status_payload_does_not_expose_secrets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            token_file = f"{tmpdir}/evotor-token.json"
            receipts_file = f"{tmpdir}/evotor-receipts.jsonl"
            save_token(token_file, "secret-token")
            save_receipt(
                receipts_file,
                {
                    "token": "secret-token",
                    "deviceId": "terminal-1",
                    "totalAmount": 120,
                },
            )

            payload = evotor_status_payload(
                type(
                    "SettingsStub",
                    (),
                    {
                        "evotor_token_file": token_file,
                        "evotor_receipts_file": receipts_file,
                        "evotor_receipts_enabled": True,
                    },
                )()
            )

            self.assertTrue(payload["token_received"])
            self.assertTrue(payload["receipts_enabled"])
            self.assertEqual(payload["receipts_count"], 1)
            self.assertIn("deviceId", payload["last_receipt_keys"])
            self.assertNotIn("token", payload["last_receipt_keys"])
