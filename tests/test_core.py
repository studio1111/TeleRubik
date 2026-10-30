import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from telerubik.adapters import RubikaSender
from telerubik.cli import _message_kind
from telerubik.config import ConfigurationError, Settings
from telerubik.engine import TransferEngine
from telerubik.queue import Job, TransferQueue
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
        self.assertEqual(settings.telegram_session_string, "")
    def test_config_can_explicitly_disable_dry_run(self):
        settings = Settings.from_env({**BASE_ENV, "TELERUBIK_DRY_RUN": "false"})
        self.assertFalse(settings.dry_run)
    def test_config_rejects_ambiguous_dry_run_value(self):
        with self.assertRaisesRegex(ConfigurationError, "TELERUBIK_DRY_RUN"):
            Settings.from_env({**BASE_ENV, "TELERUBIK_DRY_RUN": "sometimes"})

class RemoteSessionConfigTests(unittest.TestCase):
    def test_config_accepts_telegram_string_session_from_environment(self):
        settings = Settings.from_env({**BASE_ENV, "TELEGRAM_SESSION_STRING": "session-string-secret"})
        self.assertEqual(settings.telegram_session_string, "session-string-secret")


class MessageClassificationTests(unittest.TestCase):
    def test_text_message_is_transferable_without_media(self):
        self.assertEqual(_message_kind(SimpleNamespace(media=None, file=None, message="hello")), "text")
    def test_downloadable_media_is_classified_as_media(self):
        self.assertEqual(_message_kind(SimpleNamespace(media=object(), file=object(), message="caption")), "media")
    def test_non_downloadable_preview_falls_back_to_its_text(self):
        self.assertEqual(_message_kind(SimpleNamespace(media=object(), file=None, message="article link")), "text")
    def test_empty_unsupported_media_is_skipped(self):
        self.assertIsNone(_message_kind(SimpleNamespace(media=object(), file=None, message="")))

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
    def test_interrupted_processing_job_is_requeued_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 7, "resume.bin")
            queue.next_pending()
            self.assertEqual(queue.recover_processing(), 1)
            resumed = queue.next_pending()
            self.assertEqual(resumed.status, "processing")
            self.assertEqual(resumed.attempts, 2)
    def test_queue_fails_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 1, "file.bin")
            job = queue.next_pending()
            self.assertEqual(queue.retry_or_fail(job.id, 1, "permanent"), "failed")
            self.assertEqual(queue.stats()["failed"], 1)
    def test_source_cursor_defaults_to_zero_and_advances_monotonically(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            self.assertEqual(queue.get_cursor("@channel"), 0)
            queue.set_cursor("@channel", 12)
            queue.set_cursor("@channel", 9)
            self.assertEqual(queue.get_cursor("@channel"), 12)
    def test_source_cursor_rejects_negative_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            with self.assertRaises(ValueError):
                queue.set_cursor("@channel", -1)
    def test_defer_returns_claimed_job_to_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 2, "text.txt", "hello", {"kind": "text"})
            job = queue.next_pending()
            queue.defer(job.id)
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["processing"], 0)

class EngineTests(unittest.TestCase):
    def test_dry_run_validates_then_cleans_file_and_keeps_job_pending(self):
        async def scenario(tmp):
            root = Path(tmp) / "downloads"
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 8, "video.mp4", "caption", {"kind":"media", "media_type":"video"})
            sent = []
            async def download(job, directory):
                path = directory / "video.mp4"
                path.write_bytes(b"test-media")
                return path
            async def send(path, caption, job): sent.append((path, caption, job))
            engine = TransferEngine(queue, root, 100, 1, 1, dry_run=True)
            self.assertFalse(await engine.process_one(download, send))
            self.assertEqual(sent, [])
            self.assertEqual(queue.stats()["completed"], 0)
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(list(root.iterdir()), [])
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(scenario(tmp))

    def test_text_job_sends_without_downloading_media_and_logs_completion(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 10, "telegram-10", "plain text", {"kind":"text"})
            async def download(job, directory): self.fail("text job must not download a file")
            sent = []
            async def send(path, caption, job): sent.append((path, caption, job.payload["kind"]))
            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=False)
            with self.assertLogs("telerubik.engine", level="INFO") as captured:
                self.assertTrue(await engine.process_one(download, send))
            self.assertEqual(sent, [(None, "plain text", "text")])
            self.assertEqual(queue.stats()["completed"], 1)
            self.assertTrue(any("Processing job=" in line for line in captured.output))
            self.assertTrue(any("Sending job=" in line for line in captured.output))
            self.assertTrue(any("Transfer completed" in line for line in captured.output))
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(scenario(tmp))

    def test_rejects_download_path_outside_configured_directory(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 9, "bad.bin")
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"not-sent")
            async def download(job, directory): return outside
            async def send(path, caption, job): self.fail("must not send a path outside the download directory")
            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=False)
            await engine.process_one(download, send)
            self.assertEqual(queue.stats()["failed"], 1)
            self.assertTrue(outside.exists())
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(scenario(tmp))

class AdapterTests(unittest.TestCase):
    def test_text_job_routes_to_send_message(self):
        async def scenario():
            class FakeClient:
                def __init__(self): self.calls = []
                async def send_message(self, *args): self.calls.append(("message", args))
            fake = FakeClient()
            sender = RubikaSender("token", "destination")
            sender.client = fake
            job = Job(1, "chat", 1, "telegram-1", "hello", "processing", 1, {"kind":"text"})
            await sender.send_job(None, "hello", job)
            self.assertEqual(fake.calls, [("message", ("destination", "hello"))])
        asyncio.run(scenario())

    def test_media_jobs_use_documented_rubpy_type_values(self):
        async def scenario():
            class FakeClient:
                def __init__(self): self.calls = []
                async def send_file(self, *args, **kwargs): self.calls.append((args, kwargs))
            fake = FakeClient()
            sender = RubikaSender("token", "destination")
            sender.client = fake
            path = Path("sample.bin")
            for media_type, expected in (("photo", "Image"), ("video", "Video"), ("file", "File")):
                job = Job(1, "chat", 1, path.name, "caption", "processing", 1, {"kind":"media", "media_type":media_type})
                await sender.send_job(path, "caption", job)
                self.assertEqual(fake.calls[-1][0], ("destination",))
                self.assertEqual(fake.calls[-1][1], {"file":str(path), "text":"caption", "file_name":path.name, "type":expected})
        asyncio.run(scenario())

if __name__ == "__main__": unittest.main()
