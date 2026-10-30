from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telerubik.engine import TransferEngine
from telerubik.queue import TransferQueue


class PersistentDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def _run_job(self, sender):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        downloads = root / "downloads"
        downloads.mkdir()
        cached = downloads / "1-movie.mkv"
        cached.write_bytes(b"verified-media-payload")
        cached.with_name(cached.name + ".sha256").write_text("test-hash\n", encoding="ascii")
        queue = TransferQueue(root / "state.sqlite3")
        queue.enqueue("@source", 1734, "movie.mkv", "caption", {"kind": "media", "media_type": "video"})
        engine = TransferEngine(
            queue=queue,
            download_dir=downloads,
            max_file_size_bytes=1024,
            max_retries=1,
            retry_base_seconds=0,
            dry_run=False,
        )

        async def downloader(job, directory):
            return cached

        await engine.process_one(downloader, sender)
        return cached, queue.stats()

    async def test_failed_upload_retains_validated_download_for_restart_retry(self):
        async def failing_sender(path, caption, job):
            raise RuntimeError("simulated network outage")

        cached, stats = await self._run_job(failing_sender)
        self.assertTrue(cached.is_file())
        self.assertTrue(cached.with_name(cached.name + ".sha256").is_file())
        self.assertEqual(stats["failed"], 1)

    async def test_successful_upload_removes_download_and_integrity_sidecar(self):
        async def successful_sender(path, caption, job):
            self.assertTrue(path.is_file())
            self.assertEqual(caption, "caption")

        cached, stats = await self._run_job(successful_sender)
        self.assertFalse(cached.exists())
        self.assertFalse(cached.with_name(cached.name + ".sha256").exists())
        self.assertEqual(stats["completed"], 1)


if __name__ == "__main__":
    unittest.main()
