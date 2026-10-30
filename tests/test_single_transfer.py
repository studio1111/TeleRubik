import asyncio
import tempfile
import unittest
from pathlib import Path

from telerubik.engine import TransferEngine
from telerubik.queue import TransferQueue


class SingleTransferIsolationTests(unittest.TestCase):
    def test_filtered_claim_leaves_every_other_pending_job_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("@animehighbo", 1731, "other.mkv", payload={"kind": "media"})
            queue.enqueue("@animehighbo", 1734, "Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv", payload={"kind": "media"})
            queue.enqueue("@animehighbo", 1735, "later.mkv", payload={"kind": "media"})

            selected = queue.next_pending(source_chat="@animehighbo", message_id=1734)

            self.assertEqual(selected.message_id, 1734)
            self.assertEqual(queue.stats()["pending"], 2)
            self.assertEqual(queue.stats()["processing"], 1)
            self.assertEqual(queue.next_pending(source_chat="@animehighbo", message_id=9999), None)

    def test_engine_processes_only_the_requested_message(self):
        async def scenario(tmp):
            queue = TransferQueue(Path(tmp) / "queue.sqlite3")
            queue.enqueue("@animehighbo", 1731, "other.mkv", payload={"kind": "media"})
            queue.enqueue("@animehighbo", 1734, "Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv", "Spider-Man", {"kind": "media"})
            sent = []

            async def download(job, directory):
                path = directory / job.filename
                path.write_bytes(b"spiderman")
                return path

            async def sender(path, caption, job):
                sent.append(job.message_id)

            engine = TransferEngine(queue, Path(tmp) / "downloads", 2048 * 1024 * 1024, 1, 1)
            self.assertTrue(await engine.process_one(download, sender, source_chat="@animehighbo", message_id=1734))
            self.assertEqual(sent, [1734])
            self.assertEqual(queue.stats()["completed"], 1)
            self.assertEqual(queue.stats()["pending"], 1)
            self.assertEqual(queue.stats()["processing"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(scenario(tmp))


if __name__ == "__main__":
    unittest.main()
