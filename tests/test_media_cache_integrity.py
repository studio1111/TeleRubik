import tempfile
import unittest
from pathlib import Path

from telerubik.security import verify_cached_file, write_cache_sidecar


class MediaCacheIntegrityTests(unittest.TestCase):
    def test_cache_with_matching_size_and_sha256_is_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "movie.mkv"
            path.write_bytes(b"movie-data")
            write_cache_sidecar(path)
            self.assertTrue(verify_cached_file(path, len(b"movie-data")))

    def test_cache_without_sidecar_is_not_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "movie.mkv"
            path.write_bytes(b"movie-data")
            self.assertFalse(verify_cached_file(path, len(b"movie-data")))

    def test_same_size_modified_cache_is_not_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "movie.mkv"
            path.write_bytes(b"movie-data")
            write_cache_sidecar(path)
            path.write_bytes(b"other-data")
            self.assertEqual(path.stat().st_size, len(b"movie-data"))
            self.assertFalse(verify_cached_file(path, len(b"movie-data")))

    def test_cache_with_wrong_expected_size_is_not_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "movie.mkv"
            path.write_bytes(b"movie-data")
            write_cache_sidecar(path)
            self.assertFalse(verify_cached_file(path, len(b"movie-data") + 1))


if __name__ == "__main__":
    unittest.main()
