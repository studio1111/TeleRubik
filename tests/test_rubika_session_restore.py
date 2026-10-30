from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.restore_rubika_session import restore_session
from scripts.export_rubika_session import export_session


def make_session(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE version (version integer primary key);
        INSERT INTO version VALUES (1);
        CREATE TABLE session (
            phone text primary key, auth text, guid text, agent text, private_key text
        );
        INSERT INTO session VALUES ('phone', 'auth-secret', 'guid', 'agent', 'private-key');
        """
    )
    connection.commit()
    connection.close()
    return path.read_bytes()


class RubikaSessionRestoreTests(unittest.TestCase):

    def test_exports_authenticated_session_to_private_base64_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.rp"
            raw = make_session(source)
            output = root / "private" / "session.rp.b64"
            result = export_session(source, output)
            self.assertEqual(base64.b64decode(result.read_bytes()), raw)
            self.assertEqual(os.stat(result).st_mode & 0o777, 0o600)

    def test_export_rejects_non_session_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "other.rp"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE unrelated (value text)")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(ValueError, "not a Rubpy"):
                export_session(source, root / "out.b64")

    def test_restores_valid_rubpy_database_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.rp"
            raw = make_session(source)
            destination = root / "private" / "rubika_session.rp"
            result = restore_session(base64.b64encode(raw).decode(), destination)
            self.assertEqual(result.read_bytes(), raw)
            self.assertEqual(os.stat(result).st_mode & 0o777, 0o600)

    def test_rejects_invalid_base64(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "base64"):
                restore_session("not base64!", Path(directory) / "session.rp")

    def test_rejects_non_sqlite_data(self):
        with tempfile.TemporaryDirectory() as directory:
            encoded = base64.b64encode(b"not a database" * 20).decode()
            with self.assertRaisesRegex(ValueError, "SQLite"):
                restore_session(encoded, Path(directory) / "session.rp")

    def test_rejects_sqlite_without_rubpy_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "other.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE unrelated (value text)")
            connection.commit()
            connection.close()
            encoded = base64.b64encode(source.read_bytes()).decode()
            with self.assertRaisesRegex(ValueError, "Rubpy session tables"):
                restore_session(encoded, root / "session.rp")

    def test_does_not_overwrite_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.rp"
            raw = make_session(source)
            target = root / "existing.rp"
            target.write_bytes(b"keep")
            link = root / "link.rp"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                restore_session(base64.b64encode(raw).decode(), link)
            self.assertEqual(target.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
