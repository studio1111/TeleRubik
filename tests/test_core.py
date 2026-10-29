import tempfile
import unittest
from pathlib import Path
from telerubik.config import ConfigurationError, Settings
from telerubik.queue import TransferQueue
from telerubik.security import safe_filename, validate_file_size

BASE_ENV = {"TELEGRAM_API_ID":"12345", "TELEGRAM_API_HASH":"fake-hash", "TELEGRAM_SOURCE_CHATS":"@allowed_channel, -100123", "RUBIKA_BOT_TOKEN":"placeholder-token", "RUBIKA_DESTINATION_GUID":"placeholder-guid"}

class SecurityTests(unittest.TestCase):
    def test_filename_drops_path_traversal(self): self.assertEqual(safe_filename("../../../../etc/passwd"), "passwd")
    def test_filename_handles_empty_and_device_names(self):
        self.assertEqual(safe_filename(""), "telegram-file")
        self.assertEqual(safe_filename("CON"), "_CON")
    def test_file_limit_accepts_exact_limit_and_rejects_oversize(self):
        self.assertTrue(validate_file_size(100, 100))
        self.assertFalse(validate_file_size(101, 100))
        self.assertFalse(validate_file_size(None, 100))
        self.assertFalse(validate_file_size(-1, 100))
    def test_config_requires_required_values(self):
        with self.assertRaises(ConfigurationError): Settings.from_env({"TELEGRAM_API_ID":"1"})
    def test_config_parses_safe_defaults(self):
        settings = Settings.from_env(BASE_ENV)
        self.assertTrue(settings.dry_run)
        self.assertEqual(settings.telegram_source_chats, ("@allowed_channel", "-100123"))
        self.assertEqual(settings.max_retries, 3)

class QueueTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_transitions_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            self.assertTrue(queue.enqueue("@channel", 42, "file.txt", "caption"))
            self.assertFalse(queue.enqueue("@channel", 42, "file.txt", "caption"))
            job = queue.next_pending()
            self.assertEqual(job.status, "processing")
            self.assertEqual(job.attempts, 1)
            self.assertEqual(queue.retry_or_fail(job.id, 3, "network"), "pending")
            retry = queue.next_pending()
            self.assertEqual(retry.attempts, 2)
            queue.complete(retry.id)
            self.assertEqual(queue.stats()["completed"], 1)
            self.assertEqual(queue.stats()["pending"], 0)
    def test_queue_fails_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 1, "file.bin")
            job = queue.next_pending()
            self.assertEqual(queue.retry_or_fail(job.id, 1, "permanent"), "failed")
            self.assertEqual(queue.stats()["failed"], 1)

if __name__ == "__main__": unittest.main()
