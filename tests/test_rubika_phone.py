from __future__ import annotations

import unittest

from scripts.request_rubika_code import normalize_international_phone, safe_error_fields


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


if __name__ == "__main__":
    unittest.main()
