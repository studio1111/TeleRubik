"""Export a closed Rubpy SQLite session as base64 for a GitHub Actions secret.

Run only after disconnecting the Rubpy client. The output file is sensitive.
"""
from __future__ import annotations

import argparse
import base64
import os
import sqlite3
from pathlib import Path


def export_session(source: Path, destination: Path) -> Path:
    source = source.expanduser()
    destination = destination.expanduser()
    if not source.is_file() or source.is_symlink():
        raise ValueError("Rubpy session file does not exist or is not a regular file")
    if source.stat().st_size < 100:
        raise ValueError("Rubpy session file is unexpectedly small")
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("Rubpy session database failed integrity check")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"version", "session"}.issubset(tables):
            raise ValueError("File is not a Rubpy SQLite session")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session)")}
        required = {"phone", "auth", "guid", "agent", "private_key"}
        if not required.issubset(columns):
            raise ValueError("Rubpy session schema is incomplete")
        row = connection.execute(
            "SELECT phone, auth, guid FROM session LIMIT 1"
        ).fetchone()
        if not row or not all(isinstance(value, str) and value for value in row):
            raise ValueError("Rubpy session is not authenticated")
    finally:
        connection.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(source.read_bytes())
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(destination, 0o600)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="private/rubika_session.rp")
    parser.add_argument("--output", default="private/rubika_session.rp.b64")
    args = parser.parse_args()
    path = export_session(Path(args.source), Path(args.output))
    print(f"Secret export created locally: {path}")
    print("Do not commit, upload, or send this file in chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
