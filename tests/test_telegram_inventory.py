import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.telegram_inventory import classify_quality, message_record


class InventoryTests(unittest.TestCase):
    def test_quality_detection(self):
        self.assertEqual(classify_quality("Anime.Episode.1080p.WEB-DL.mkv"), "1080p")
        self.assertEqual(classify_quality("Anime.720p.mkv"), "720p")
        self.assertEqual(classify_quality("Anime.2160p.mkv"), "2160p")
        self.assertEqual(classify_quality("Anime.Episode.WEB-DL.mkv"), "unknown")

    def test_media_record(self):
        message = SimpleNamespace(
            id=42,
            date=datetime(2026, 10, 30, tzinfo=timezone.utc),
            file=SimpleNamespace(name="Episode.1080p.mkv", size=150 * 1024 * 1024, mime_type="video/x-matroska"),
        )
        record = message_record(message)
        self.assertEqual(record["message_id"], 42)
        self.assertEqual(record["filename"], "Episode.1080p.mkv")
        self.assertEqual(record["quality"], "1080p")
        self.assertEqual(record["size_mib"], 150.0)

    def test_non_file_message_is_ignored(self):
        self.assertIsNone(message_record(SimpleNamespace(id=1, file=None)))


if __name__ == "__main__":
    unittest.main()
