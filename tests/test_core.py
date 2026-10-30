import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from telerubik.config import ConfigurationError, Settings
from telerubik.cli import _iter_source_messages
from telerubik.engine import TransferEngine
from telerubik.queue import TransferQueue
from telerubik.security import safe_filename, validate_file_size, write_cache_sidecar

BASE_ENV = {"TELEGRAM_API_ID":"12345", "TELEGRAM_API_HASH":"fake-hash", "TELEGRAM_SOURCE_CHATS":"@allowed_channel, -100123", "RUBIKA_SESSION_NAME":"./private/rubika_session", "RUBIKA_DESTINATION_GUID":"placeholder-guid"}

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

class EngineTests(unittest.TestCase):
    def test_dry_run_leaves_media_pending_and_live_run_can_send_it(self):
        async def scenario(tmp):
            root = Path(tmp) / "downloads"
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 8, "video.mp4", "caption", {"text_only": False})
            sent = []
            preview_locations = []

            async def download(job, directory):
                preview_locations.append(Path(directory))
                path = Path(directory) / "video.mp4"
                path.write_bytes(b"test-media")
                return path

            async def send(path, caption):
                sent.append((path.read_bytes(), caption))

            preview = TransferEngine(queue, root, 100, 1, 1, dry_run=True)
            self.assertEqual(await preview.preview_pending(download), 1)
            self.assertEqual(sent, [])
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["processing"], 0)
            self.assertEqual(queue.stats()["completed"], 0)
            self.assertEqual(list(root.iterdir()), [])
            self.assertFalse(preview_locations[0].exists())

            # The next live run must still claim and deliver the exact same job.
            live = TransferEngine(queue, root, 100, 1, 1, dry_run=False)
            self.assertTrue(await live.process_one(download, send))
            self.assertEqual(sent, [(b"test-media", "caption")])
            self.assertEqual(queue.stats()["completed"], 1)
            self.assertEqual(queue.stats()["pending"], 0)
            self.assertEqual(list(root.iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_dry_run_text_preview_does_not_send_or_consume_job(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 11, "telegram-11", "plain text", {"text_only": True})
            sent = []

            async def download(job, directory):
                return None

            async def send(path, caption):
                sent.append((path, caption))

            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=True)
            self.assertEqual(await engine.preview_pending(download), 1)
            self.assertEqual(sent, [])
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["completed"], 0)
            self.assertEqual(queue.stats()["processing"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_dry_run_cannot_call_mutating_process_one(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 12, "file.bin", "caption")
            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=True)

            async def download(job, directory):
                return None

            async def send(path, caption):
                self.fail("dry-run must never invoke the sender")

            with self.assertRaisesRegex(RuntimeError, "preview_pending"):
                await engine.process_one(download, send)
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["processing"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_dry_run_does_not_delete_existing_verified_live_retry_cache(self):
        async def scenario(tmp):
            root = Path(tmp) / "downloads"
            root.mkdir()
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 13, "cached.bin", "caption", {"text_only": False})
            cached = root / "1-cached.bin"
            cached.write_bytes(b"verified-cache")
            sidecar = write_cache_sidecar(cached)

            async def downloader(job, directory):
                # Deliberately return an existing path to exercise the boundary:
                # preview must reject it without deleting the live retry cache.
                return cached

            engine = TransferEngine(queue, root, 100, 1, 1, dry_run=True)
            self.assertEqual(await engine.preview_pending(downloader), 1)
            self.assertEqual(cached.read_bytes(), b"verified-cache")
            self.assertTrue(sidecar.is_file())
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["completed"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_rejects_download_path_outside_configured_directory(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("chat", 9, "bad.bin")
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"not-sent")
            async def download(job, directory): return outside
            async def send(path, caption): self.fail("must not send a path outside the download directory")
            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=False)
            await engine.process_one(download, send)
            self.assertEqual(queue.stats()["failed"], 1)
            self.assertTrue(outside.exists())
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(scenario(tmp))

    def test_failure_does_not_persist_raw_exception_message(self):
        async def scenario(tmp):
            queue_path = Path(tmp) / "queue.sqlite3"
            queue = TransferQueue(queue_path)
            queue.enqueue("chat", 10, "post.txt", "caption", {"text_only": True})

            async def download(job, directory):
                return None

            async def send(path, caption):
                raise RuntimeError("private-token=must-never-be-stored")

            engine = TransferEngine(queue, Path(tmp) / "downloads", 100, 1, 1, dry_run=False)
            await engine.process_one(download, send)
            db = sqlite3.connect(queue_path)
            try:
                stored_error = db.execute("SELECT last_error FROM jobs").fetchone()[0]
            finally:
                db.close()
            self.assertEqual(stored_error, "RuntimeError")
            self.assertNotIn("must-never-be-stored", stored_error)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))


class SourceCursorTests(unittest.TestCase):
    def test_cursor_is_monotonic_and_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            self.assertEqual(queue.get_cursor("@channel"), 0)
            queue.set_cursor("@channel", 12)
            queue.set_cursor("@channel", 9)
            self.assertEqual(queue.get_cursor("@channel"), 12)

    def test_negative_cursor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            with self.assertRaises(ValueError):
                queue.set_cursor("@channel", -1)

    def test_first_scan_is_bounded_and_yields_oldest_first(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def iter_messages(self, chat, **kwargs):
                self.calls.append((chat, kwargs))

                async def messages():
                    for message_id in (30, 20, 10):
                        yield SimpleNamespace(id=message_id)

                return messages()

        async def scenario():
            client = FakeClient()
            result = [
                message.id
                async for message in _iter_source_messages(client, "@channel", 0, 3)
            ]
            self.assertEqual(result, [10, 20, 30])
            self.assertEqual(client.calls, [("@channel", {"limit": 3})])

        asyncio.run(scenario())

    def test_incremental_scan_uses_cursor_without_history_limit(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def iter_messages(self, chat, **kwargs):
                self.calls.append((chat, kwargs))

                async def messages():
                    for message_id in (101, 102, 103):
                        yield SimpleNamespace(id=message_id)

                return messages()

        async def scenario():
            client = FakeClient()
            result = [
                message.id
                async for message in _iter_source_messages(client, -100123, 100, 25)
            ]
            self.assertEqual(result, [101, 102, 103])
            self.assertEqual(client.calls, [(-100123, {"min_id": 100, "reverse": True})])

        asyncio.run(scenario())

if __name__ == "__main__": unittest.main()
