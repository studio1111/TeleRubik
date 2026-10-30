from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from .queue import Job, TransferQueue
from .security import cache_sidecar_path, safe_filename, sha256_file, validate_file_size

log = logging.getLogger(__name__)
Downloader = Callable[[Job, Path], Awaitable[Path | None]]
Sender = Callable[[Path | None, str], Awaitable[None]]


class TransferEngine:
    def __init__(
        self,
        queue: TransferQueue,
        download_dir: Path,
        max_file_size_bytes: int,
        max_retries: int,
        retry_base_seconds: int,
        dry_run: bool = False,
    ):
        self.queue, self.download_dir = queue, download_dir
        self.max_file_size_bytes, self.max_retries = max_file_size_bytes, max_retries
        self.retry_base_seconds, self.dry_run = retry_base_seconds, dry_run
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def process_one(self, downloader: Downloader, sender: Sender) -> bool:
        job = self.queue.next_pending()
        if job is None:
            return False
        path: Path | None = None
        transfer_completed = False
        try:
            downloaded = await downloader(job, self.download_dir)
            if downloaded is None:
                if job.payload.get("text_only") is not True or not job.caption.strip():
                    raise RuntimeError("Source message has no downloadable file or text")
                if self.dry_run:
                    log.info("DRY RUN: text-only job=%s chars=%s", job.id, len(job.caption))
                else:
                    await sender(None, job.caption)
            else:
                path = Path(downloaded).resolve()
                root = self.download_dir.resolve()
                if root not in path.parents:
                    raise RuntimeError("Downloader returned a path outside the download directory")
                if not path.is_file():
                    raise RuntimeError("Downloaded file does not exist")
                size = path.stat().st_size
                if not validate_file_size(size, self.max_file_size_bytes):
                    raise RuntimeError("File size exceeds configured limit or is invalid")
                if self.dry_run:
                    log.info(
                        "DRY RUN: job=%s name=%s bytes=%s sha256=%s",
                        job.id, safe_filename(job.filename), size, sha256_file(path),
                    )
                else:
                    await sender(path, job.caption)
            self.queue.complete(job.id)
            transfer_completed = True
            return True
        except Exception as exc:
            status = self.queue.retry_or_fail(
                job.id, self.max_retries, type(exc).__name__
            )
            log.warning("Transfer job %s %s: %s", job.id, status, type(exc).__name__)
            if status == "pending":
                await asyncio.sleep(
                    self.retry_base_seconds * min(2 ** max(0, job.attempts - 1), 16)
                )
            return True
        finally:
            if path is not None and path.is_file():
                try:
                    root = self.download_dir.resolve()
                    # Keep verified files after live upload failures so retries and
                    # process restarts can reuse them. Dry-run and successful sends
                    # clean both the file and its integrity sidecar.
                    if root in path.parents and (transfer_completed or self.dry_run):
                        path.unlink()
                        cache_sidecar_path(path).unlink(missing_ok=True)
                except OSError:
                    log.warning("Could not clean temporary file for job %s", job.id)
