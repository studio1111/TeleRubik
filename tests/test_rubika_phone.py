from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.request_rubika_code import (
    _validate_session_target,
    authenticate_client,
    normalize_international_phone,
    RubikaResponseError,
    safe_error_fields,
)


class RubikaPhoneNormalizationTests(unittest.TestCase):
    def test_plus_prefixed_number_becomes_digits_only(self):
        self.assertEqual(normalize_international_phone("+989121234567"), "989121234567")

    def test_international_00_prefix_is_supported(self):
        self.assertEqual(normalize_international_phone("00491234567890"), "491234567890")

    def test_spaces_and_persian_digits_are_normalized(self):
        self.assertEqual(normalize_international_phone("+۹۸ ۹۱۲ ۱۲۳ ۴۵۶۷"), "989121234567")

    def test_local_number_without_country_code_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "international format"):
            normalize_international_phone("09121234567")

    def test_invalid_length_and_non_digits_are_rejected(self):
        for value in ("+123", "+98ABC1234567", "+0123456789"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_international_phone(value)

    def test_error_reporting_only_returns_safe_api_fields(self):
        class ApiError(Exception):
            status = None
            status_det = None

        error = ApiError({"status": "ERROR_GENERIC", "status_det": "INVALID_INPUT", "phone": "private"})
        self.assertEqual(safe_error_fields(error), ("ERROR_GENERIC", "INVALID_INPUT"))

    def test_error_reporting_extracts_stringified_rubpy_response(self):
        error = RuntimeError("{'status': 'ERROR_GENERIC', 'status_det': 'INVALID_INPUT', 'phone': 'private'}")
        self.assertEqual(safe_error_fields(error), ("ERROR_GENERIC", "INVALID_INPUT"))

    def test_error_reporting_rejects_untrusted_identifier_text(self):
        error = RuntimeError("{'status': 'private phone 123', 'status_det': 'INVALID_INPUT'}")
        self.assertEqual(safe_error_fields(error), ("unknown", "INVALID_INPUT"))

    def test_session_target_rejects_authenticated_existing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.rp"
            db = sqlite3.connect(path)
            db.executescript(
                "CREATE TABLE version(version integer primary key);"
                "INSERT INTO version VALUES (1);"
                "CREATE TABLE session(phone text, auth text, guid text, agent text, private_key text);"
                "INSERT INTO session VALUES ('phone', 'auth', 'guid', 'agent', 'key');"
            )
            db.commit()
            db.close()
            with self.assertRaisesRegex(ValueError, "already exists"):
                _validate_session_target(str(path))

    def test_session_target_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.rp"
            target.write_text("not used")
            link = root / "link.rp"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                _validate_session_target(str(link))


class RubikaTwoStepLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_sendpasskey_uses_hidden_password_then_completes_session(self):
        class FakeSession:
            def __init__(self):
                self.inserted = None

            def insert(self, **kwargs):
                self.inserted = kwargs

        class FakeClient:
            def __init__(self):
                self.session = FakeSession()
                self.name = "private/rubika_session"
                self.user_agent = "test-agent"
                self.calls = []
                self.code_results = [
                    SimpleNamespace(status="SendPassKey", hint_pass_key="***"),
                    SimpleNamespace(status="OK", phone_code_hash="opaque-hash"),
                ]

            async def send_code(self, **kwargs):
                self.calls.append(("send_code", kwargs))
                return self.code_results.pop(0)

            async def sign_in(self, **kwargs):
                self.calls.append(("sign_in", kwargs))
                return SimpleNamespace(
                    status="OK",
                    auth="encrypted-auth",
                    user=SimpleNamespace(user_guid="user-guid", phone="989121234567"),
                )

            async def register_device(self, **kwargs):
                self.calls.append(("register_device", kwargs))

            async def get_me(self):
                self.calls.append(("get_me", {}))
                return SimpleNamespace(user=SimpleNamespace(user_guid="user-guid"))

        class FakeCrypto:
            @staticmethod
            def create_keys():
                return "public-key", "private-key"

            @staticmethod
            def decrypt_RSA_OAEP(private_key, encrypted):
                return "a" * 32

            @staticmethod
            def passphrase(auth):
                return "derived-key"

            @staticmethod
            def decode_auth(auth):
                return "decoded-auth"

        class FakeRSA:
            @staticmethod
            def import_key(value):
                return "imported-key"

        class FakePkcs:
            @staticmethod
            def new(value):
                return "signing-key"

        prompts = []

        def password_prompt(label):
            prompts.append(label)
            return "private-password"

        def code_prompt(label):
            prompts.append(label)
            return "12345"

        client = FakeClient()
        with patch(
            "scripts.request_rubika_code._load_crypto_primitives",
            return_value=(FakeCrypto, FakeRSA, FakePkcs),
        ):
            await authenticate_client(client, "989121234567", password_prompt, code_prompt)

        self.assertEqual(client.calls[0][1], {
            "phone_number": "989121234567", "send_type": "SMS"
        })
        self.assertEqual(client.calls[1][1], {
            "phone_number": "989121234567",
            "pass_key": "private-password",
            "send_type": "SMS",
        })
        self.assertEqual(client.calls[2][1]["phone_code_hash"], "opaque-hash")
        self.assertEqual(client.calls[2][1]["phone_code"], "12345")
        self.assertEqual(client.session.inserted["auth"], "a" * 32)
        self.assertEqual(client.session.inserted["guid"], "user-guid")
        self.assertEqual([call[0] for call in client.calls][-2:], ["register_device", "get_me"])
        self.assertEqual(len(prompts), 2)

    async def test_rejected_two_step_password_exposes_only_safe_api_status(self):
        class FakeClient:
            def __init__(self):
                self.calls = []
                self.results = [
                    SimpleNamespace(status="SendPassKey"),
                    SimpleNamespace(status="ERROR_GENERIC", status_det="INVALID_PASS_KEY"),
                ]

            async def send_code(self, **kwargs):
                self.calls.append(kwargs)
                return self.results.pop(0)

        password_prompts = []
        code_prompts = []

        def password_prompt(label):
            password_prompts.append(label)
            return "wrong-password"

        def code_prompt(label):
            code_prompts.append(label)
            raise AssertionError("OTP must not be requested after a rejected pass key")

        client = FakeClient()
        with self.assertRaisesRegex(RubikaResponseError, "did not accept"):
            await authenticate_client(client, "491234567890", password_prompt, code_prompt)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["pass_key"], "wrong-password")
        self.assertEqual(len(password_prompts), 1)
        self.assertEqual(code_prompts, [])
        self.assertEqual(
            safe_error_fields(RubikaResponseError(
                "rejected",
                SimpleNamespace(status="ERROR_GENERIC", status_det="INVALID_PASS_KEY"),
            )),
            ("ERROR_GENERIC", "INVALID_PASS_KEY"),
        )

    async def test_empty_two_step_password_stops_before_second_request(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def send_code(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(status="SendPassKey")

        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            await authenticate_client(client, "989121234567", lambda _: "", lambda _: "12345")
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
