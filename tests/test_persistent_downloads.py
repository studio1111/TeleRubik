from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from telerubik.engine import TransferEngine
from telerubik.queue import TransferQueue
from telerubik.security import write_cache_sidecar


class PersistentDownloadTests(unittest.TestCase):
    def test_failed_upload_retains_verified_file_then_success_cleans_it(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                downloads = root / "downloads"
                downloads.mkdir()
                queue = TransferQueue(root / "state.sqlite3")
                queue.enqueue("source", 12, "clip.mkv", "caption", {"expected_size": 5})
                cached = downloads / "1-clip.mkv"
                cached.write_bytes(b"video")
                write_cache_sidecar(cached)
                engine = TransferEngine(queue, downloads, 1024, 3, 1, dry_run=False)

                async def downloader(job, target):
                    return cached

                async def fail_sender(path, caption):
                    raise RuntimeError("simulated upload failure")

                await engine.process_one(downloader, fail_sender)
                self.assertTrue(cached.is_file())
                self.assertTrue(Path(str(cached) + ".sha256.json").is_file())
                self.assertEqual(queue.stats()["pending"], 1)

                sent = []

                async def success_sender(path, caption):
                    sent.append((path.read_bytes(), caption))

                await engine.process_one(downloader, success_sender)
                self.assertEqual(sent, [(b"video", "caption")])
                self.assertFalse(cached.exists())
                self.assertFalse(Path(str(cached) + ".sha256.json").exists())
                self.assertEqual(queue.stats()["completed"], 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
