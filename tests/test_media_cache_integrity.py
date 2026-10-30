from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telerubik.security import cache_sidecar_path, verify_cached_file, write_cache_sidecar


class MediaCacheIntegrityTests(unittest.TestCase):
    def test_valid_cache_and_expected_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media.bin"
            path.write_bytes(b"validated-media")
            write_cache_sidecar(path)
            self.assertTrue(verify_cached_file(path, len(b"validated-media")))
            self.assertFalse(verify_cached_file(path, len(b"wrong")))

    def test_missing_sidecar_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media.bin"
            path.write_bytes(b"media")
            self.assertFalse(verify_cached_file(path))

    def test_same_size_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media.bin"
            path.write_bytes(b"first")
            sidecar = write_cache_sidecar(path)
            path.write_bytes(b"other")
            self.assertEqual(sidecar, cache_sidecar_path(path))
            self.assertFalse(verify_cached_file(path))


if __name__ == "__main__":
    unittest.main()
