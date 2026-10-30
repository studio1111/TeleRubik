from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from telerubik.chunks import reassemble, split_file


class ChunkUtilityTests(unittest.TestCase):
    def test_split_and_reassemble_preserves_bytes_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.mkv"
            payload = bytes(range(256)) * 41
            source.write_bytes(payload)
            chunks = root / "chunks"
            manifest = split_file(source, chunks, 1024)
            self.assertEqual(manifest["total_bytes"], len(payload))
            self.assertEqual(manifest["total_parts"], 11)
            self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
            for part in manifest["parts"]:
                part_path = chunks / part["filename"]
                self.assertEqual(part_path.stat().st_size, part["size_bytes"])
                self.assertEqual(
                    hashlib.sha256(part_path.read_bytes()).hexdigest(), part["sha256"]
                )
            restored = reassemble(
                chunks / f"{source.name}.manifest.json", chunks, root / "restored.mkv"
            )
            self.assertEqual(restored.read_bytes(), payload)

    def test_empty_file_has_one_valid_empty_part(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "empty.bin"
            source.write_bytes(b"")
            manifest = split_file(source, root / "parts", 32)
            self.assertEqual(manifest["total_parts"], 1)
            self.assertEqual(manifest["parts"][0]["size_bytes"], 0)
            reassemble(
                root / "parts" / "empty.bin.manifest.json",
                root / "parts",
                root / "restored.bin",
            )
            self.assertEqual((root / "restored.bin").read_bytes(), b"")

    def test_rejects_invalid_chunk_size_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"data")
            with self.assertRaises(ValueError):
                split_file(source, root / "bad", 0)
            chunks = root / "chunks"
            split_file(source, chunks, 2)
            with self.assertRaises(FileExistsError):
                split_file(source, chunks, 2)

    def test_reassembly_detects_corrupted_part_and_preserves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"abcdefgh")
            chunks = root / "chunks"
            manifest = split_file(source, chunks, 3)
            (chunks / manifest["parts"][0]["filename"]).write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "integrity"):
                reassemble(
                    chunks / "source.bin.manifest.json", chunks, root / "restored.bin"
                )
            self.assertFalse((root / "restored.bin").exists())
            self.assertFalse((root / "restored.bin.telerubik-partial").exists())

    def test_manifest_cannot_escape_parts_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"abc")
            chunks = root / "chunks"
            manifest = split_file(source, chunks, 3)
            manifest["parts"][0]["filename"] = "../source.bin"
            (chunks / "source.bin.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                reassemble(
                    chunks / "source.bin.manifest.json", chunks, root / "restored.bin"
                )


if __name__ == "__main__":
    unittest.main()
