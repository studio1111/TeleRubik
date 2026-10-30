"""Restore a Rubpy SQLite session from a GitHub Actions secret.

The secret must contain base64 of the complete *.rp SQLite session file.
Never print the secret or session contents.
"""
from __future__ import annotations

import base64
import binascii
import os
import sqlite3
import tempfile
from pathlib import Path


def restore_session(encoded: str, destination: Path) -> Path:
    if not encoded or not encoded.strip():
        raise ValueError("RUBIKA_SESSION_B64 is not configured")
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("RUBIKA_SESSION_B64 is not valid base64") from exc
    if len(raw) < 100 or not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("Decoded Rubpy session is not a valid SQLite database")

    destination = destination.expanduser()
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("Refusing to overwrite a symlink session path")

    fd, temporary_name = tempfile.mkstemp(prefix=".rubika-session-", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Validate database integrity and expected Rubpy schema before replacing.
        connection = sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)
        try:
            check = connection.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise ValueError("Rubpy session database failed integrity check")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"version", "session"}.issubset(tables):
                raise ValueError("SQLite file does not contain the expected Rubpy session tables")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(session)")
            }
            if not {"phone", "auth", "guid", "agent", "private_key"}.issubset(columns):
                raise ValueError("Rubpy session schema is incomplete")
            row = connection.execute("SELECT phone, auth, guid FROM session LIMIT 1").fetchone()
            if not row or not all(isinstance(value, str) and value for value in row):
                raise ValueError("Rubpy session does not contain authenticated account data")
        finally:
            connection.close()
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return destination
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    secret = os.environ.get("RUBIKA_SESSION_B64", "")
    destination = Path(os.environ.get("RUBIKA_SESSION_PATH", "private/rubika_session.rp"))
    restored = restore_session(secret, destination)
    # Print only a generic success message and a non-secret path.
    print(f"Rubpy session restored and validated at {restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
