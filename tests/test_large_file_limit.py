import unittest

from telerubik.config import ConfigurationError, Settings


BASE_ENV = {
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": "test-hash",
    "TELEGRAM_SOURCE_CHATS": "source-chat",
    "RUBIKA_BOT_TOKEN": "test-token",
    "RUBIKA_DESTINATION_GUID": "private-destination",
}


class LargeFileLimitTests(unittest.TestCase):
    def test_default_limit_is_two_gibibytes(self):
        settings = Settings.from_env(BASE_ENV)
        self.assertEqual(settings.max_file_size_bytes, 2048 * 1024 * 1024)

    def test_operator_can_configure_a_lower_limit(self):
        settings = Settings.from_env({**BASE_ENV, "TELERUBIK_MAX_FILE_SIZE_MB": "750"})
        self.assertEqual(settings.max_file_size_bytes, 750 * 1024 * 1024)

    def test_limit_above_two_gibibytes_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "cannot exceed 2048 MiB"):
            Settings.from_env({**BASE_ENV, "TELERUBIK_MAX_FILE_SIZE_MB": "2049"})

    def test_invalid_limit_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "must be an integer"):
            Settings.from_env({**BASE_ENV, "TELERUBIK_MAX_FILE_SIZE_MB": "two-gigabytes"})


if __name__ == "__main__":
    unittest.main()
