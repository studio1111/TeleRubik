import asyncio
import tempfile
import unittest
from pathlib import Path
from telerubik.adapters import RubikaSender
from telerubik.cli import _validate_single_transfer_result
from telerubik.engine import TransferEngine
from telerubik.errors import PermanentTransferError
from telerubik.queue import TransferQueue

class UploadRetryTests(unittest.TestCase):
    def test_transient_upload_retries_reuse_one_download(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 1734, "large.mkv", "caption", {"kind": "media"})
            downloads, sends = [], []
            async def download(job, directory):
                downloads.append(job.message_id)
                path = directory / job.filename
                path.write_bytes(b"media")
                return path
            async def send(path, caption, job):
                sends.append(path)
                if len(sends) < 3:
                    raise OSError("temporary network interruption")
            engine = TransferEngine(queue, Path(tmp) / "downloads", 1024, 3, 0)
            await engine.process_one(download, send)
            self.assertEqual(downloads, [1734])
            self.assertEqual(len(sends), 3)
            self.assertEqual(queue.stats()["completed"], 1)
            self.assertEqual(list((Path(tmp) / "downloads").iterdir()), [])
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_http_413_is_permanent_and_is_not_retried(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "state.sqlite3")
            queue.enqueue("chat", 1734, "large.mkv", "caption", {"kind": "media"})
            downloads, sends = [], []
            async def download(job, directory):
                downloads.append(job.message_id)
                path = directory / job.filename
                path.write_bytes(b"media")
                return path
            async def send(path, caption, job):
                sends.append(path)
                raise PermanentTransferError("HTTP 413")
            engine = TransferEngine(queue, Path(tmp) / "downloads", 1024, 3, 0)
            await engine.process_one(download, send)
            self.assertEqual(downloads, [1734])
            self.assertEqual(len(sends), 1)
            self.assertEqual(queue.stats()["failed"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))

    def test_adapter_converts_http_413_to_permanent_error(self):
        async def scenario():
            class HttpError(Exception):
                status = 413
            class FakeClient:
                async def send_file(self, *args, **kwargs):
                    raise HttpError("Request Entity Too Large")
            sender = RubikaSender("token", "destination")
            sender.client = FakeClient()
            with self.assertRaises(PermanentTransferError):
                await sender.send_media(Path("large.mkv"), "caption")
        asyncio.run(scenario())

class SingleTransferResultTests(unittest.TestCase):
    def test_live_single_transfer_requires_exactly_one_completed_job(self):
        _validate_single_transfer_result({"pending": 0, "processing": 0, "completed": 1, "failed": 0}, dry_run=False)
    def test_live_single_transfer_rejects_failed_job_even_if_process_exited_zero(self):
        with self.assertRaisesRegex(RuntimeError, "completed=1"):
            _validate_single_transfer_result({"pending": 0, "processing": 0, "completed": 0, "failed": 1}, dry_run=False)
    def test_dry_run_is_valid_only_when_exact_job_remains_pending(self):
        _validate_single_transfer_result({"pending": 1, "processing": 0, "completed": 0, "failed": 0}, dry_run=True)
        with self.assertRaises(RuntimeError):
            _validate_single_transfer_result({"pending": 0, "processing": 0, "completed": 0, "failed": 1}, dry_run=True)

if __name__ == "__main__":
    unittest.main()
