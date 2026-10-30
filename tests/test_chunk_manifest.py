import hashlib
import tempfile
import unittest
from pathlib import Path

from telerubik.chunking import ChunkingError, split_file, reassemble_file


class ChunkingTests(unittest.TestCase):
    def test_split_and_reassemble_preserves_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.bin"
            payload = bytes(range(256)) * 11
            source.write_bytes(payload)

            manifest = split_file(source, root / "parts", chunk_size=333)
            output = reassemble_file(manifest, root / "restored.bin")

            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(manifest["size"], len(payload))

    def test_empty_file_is_represented_by_one_empty_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "empty.bin"
            source.write_bytes(b"")

            manifest = split_file(source, root / "parts", chunk_size=4)
            output = reassemble_file(manifest, root / "restored.bin")

            self.assertEqual(manifest["parts"], 1)
            self.assertEqual(output.read_bytes(), b"")

    def test_reassembly_rejects_modified_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.bin"
            source.write_bytes(b"abcdef")
            manifest = split_file(source, root / "parts", chunk_size=3)
            (root / "parts" / "part-000001.bin").write_bytes(b"xxx")

            with self.assertRaisesRegex(ChunkingError, "hash"):
                reassemble_file(manifest, root / "out.bin")

    def test_reassembly_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.bin"
            source.write_bytes(b"safe")
            manifest = split_file(source, root / "parts", chunk_size=4)
            manifest["part_files"][0]["name"] = "../source.bin"

            with self.assertRaises(ChunkingError):
                reassemble_file(manifest, root / "out.bin")


if __name__ == "__main__":
    unittest.main()
