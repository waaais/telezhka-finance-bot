import json
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.integrations.evotor_token_receiver import extract_token, load_token, save_token


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
