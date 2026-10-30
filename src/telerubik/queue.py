from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

@dataclass(frozen=True)
class Job:
    id: int
    source_chat: str
    message_id: int
    filename: str
    caption: str
    status: str
    attempts: int
    payload: dict[str, Any]

class TransferQueue:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_chat TEXT NOT NULL, message_id INTEGER NOT NULL,
                    filename TEXT NOT NULL, caption TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    payload_json TEXT NOT NULL DEFAULT '{}', last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_chat, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS source_cursors (
                    source_chat TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL CHECK(message_id >= 0),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a transactional SQLite connection and always close it."""
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 15000")
        try:
            with db:
                yield db
        finally:
            db.close()
    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(row["id"], row["source_chat"], row["message_id"], row["filename"], row["caption"], row["status"], row["attempts"], json.loads(row["payload_json"]))
    def enqueue(self, source_chat: str, message_id: int, filename: str, caption: str = "", payload: dict[str, Any] | None = None) -> bool:
        with self._connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO jobs(source_chat,message_id,filename,caption,payload_json) VALUES(?,?,?,?,?)", (str(source_chat), int(message_id), filename, caption, json.dumps(payload or {}, ensure_ascii=False)))
            return cursor.rowcount == 1
    def iter_pending(self) -> Iterator[Job]:
        """Yield pending jobs in stable queue order without claiming or mutating them."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status='pending' ORDER BY id"
            )
            for row in rows:
                yield self._job(row)

    def next_pending(self) -> Job | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                db.commit()
                return None
            db.execute("UPDATE jobs SET status='processing', attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            updated = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            db.commit()
            return self._job(updated)
    def complete(self, job_id: int) -> None:
        self._transition(job_id, "completed", "")
    def retry_or_fail(self, job_id: int, max_attempts: int, error: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown job {job_id}")
            status = "pending" if row["attempts"] < max_attempts else "failed"
            db.execute("UPDATE jobs SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, error[:1000], job_id))
            return status
    def _transition(self, job_id: int, status: str, error: str) -> None:
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, error, job_id))
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown job {job_id}")
    def recover_processing(self) -> int:
        """Requeue interrupted jobs at startup; run one worker instance per database."""
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE status='processing'")
            return cursor.rowcount

    def get_cursor(self, source_chat: str) -> int:
        """Return the highest message ID scanned for a source, or zero on first run."""
        with self._connect() as db:
            row = db.execute(
                "SELECT message_id FROM source_cursors WHERE source_chat=?",
                (str(source_chat),),
            ).fetchone()
        return int(row["message_id"]) if row else 0

    def set_cursor(self, source_chat: str, message_id: int) -> None:
        """Advance a source cursor monotonically after a complete scan batch."""
        if int(message_id) < 0:
            raise ValueError("message_id must be non-negative")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO source_cursors(source_chat, message_id) VALUES(?, ?)
                ON CONFLICT(source_chat) DO UPDATE SET
                    message_id=MAX(source_cursors.message_id, excluded.message_id),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (str(source_chat), int(message_id)),
            )

    def stats(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        result = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        result.update({row["status"]: row["count"] for row in rows})
        return result
