from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable
from .errors import PermanentTransferError
from .queue import Job, TransferQueue
from .security import safe_filename, sha256_file, validate_file_size

log = logging.getLogger(__name__)
Downloader = Callable[[Job, Path], Awaitable[Path | None]]
Sender = Callable[[Path | None, str, Job], Awaitable[None]]

class TransferEngine:
    def __init__(self, queue: TransferQueue, download_dir: Path, max_file_size_bytes: int, max_retries: int, retry_base_seconds: int, dry_run: bool = False):
        self.queue, self.download_dir = queue, download_dir
        self.max_file_size_bytes, self.max_retries = max_file_size_bytes, max_retries
        self.retry_base_seconds, self.dry_run = retry_base_seconds, dry_run
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def process_one(self, downloader: Downloader, sender: Sender, source_chat: str | None = None, message_id: int | None = None) -> bool:
        """Process one job; upload retries reuse the same validated local download."""
        job = self.queue.next_pending(source_chat=source_chat, message_id=message_id)
        if job is None:
            return False
        path: Path | None = None
        kind = job.payload.get("kind", "media")
        log.info("Processing job=%s source=%s message_id=%s kind=%s attempt=%s/%s",
                 job.id, job.source_chat, job.message_id, kind, job.attempts, self.max_retries)
        try:
            if kind == "text":
                if not job.caption.strip():
                    raise RuntimeError("Text job has no text content")
            else:
                log.info("Downloading media for job=%s filename=%s", job.id, safe_filename(job.filename))
                downloaded = await downloader(job, self.download_dir)
                if downloaded is None:
                    raise RuntimeError("Source message has no downloadable file")
                path = Path(downloaded).resolve()
                root = self.download_dir.resolve()
                if root not in path.parents:
                    raise RuntimeError("Downloader returned a path outside the download directory")
                if not path.is_file():
                    raise RuntimeError("Downloaded file does not exist")
                size = path.stat().st_size
                if not validate_file_size(size, self.max_file_size_bytes):
                    raise RuntimeError("File size exceeds configured limit or is invalid")
                log.info("Download validated for job=%s bytes=%s", job.id, size)

            if self.dry_run:
                if path is None:
                    log.info("DRY RUN: job=%s kind=text chars=%s", job.id, len(job.caption))
                else:
                    log.info("DRY RUN: job=%s kind=%s name=%s bytes=%s sha256=%s",
                             job.id, job.payload.get("media_type", "file"),
                             safe_filename(job.filename), path.stat().st_size, sha256_file(path))
                self.queue.defer(job.id)
                return False

            last_error: Exception | None = None
            for send_attempt in range(1, self.max_retries + 1):
                try:
                    log.info("Sending job=%s to configured Rubika destination (upload attempt %s/%s)",
                             job.id, send_attempt, self.max_retries)
                    await sender(path, job.caption, job)
                    last_error = None
                    break
                except PermanentTransferError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if send_attempt < self.max_retries:
                        delay = self.retry_base_seconds * min(2 ** (send_attempt - 1), 16)
                        log.warning("Transient upload failure job=%s attempt=%s/%s; reusing validated download; retry in %ss: %s",
                                    job.id, send_attempt, self.max_retries, delay, str(exc)[:300])
                        await asyncio.sleep(delay)
            if last_error is not None:
                status = self.queue.retry_or_fail(job.id, job.attempts, f"{type(last_error).__name__}: {last_error}")
                log.error("Upload exhausted retries job=%s status=%s error=%s", job.id, status, str(last_error)[:300])
                return True

            self.queue.complete(job.id)
            log.info("Transfer completed job=%s source=%s message_id=%s", job.id, job.source_chat, job.message_id)
            return True
        except PermanentTransferError as exc:
            status = self.queue.retry_or_fail(job.id, job.attempts, f"{type(exc).__name__}: {exc}")
            log.error("Permanent transfer failure job=%s status=%s detail=%s", job.id, status, str(exc)[:300])
            return True
        except Exception as exc:
            status = self.queue.retry_or_fail(job.id, self.max_retries, f"{type(exc).__name__}: {exc}")
            log.warning("Transfer job=%s source=%s message_id=%s status=%s error_type=%s detail=%s",
                        job.id, job.source_chat, job.message_id, status, type(exc).__name__, str(exc)[:300])
            if status == "pending":
                await asyncio.sleep(self.retry_base_seconds * min(2 ** max(0, job.attempts - 1), 16))
            return True
        finally:
            if path is not None:
                try:
                    if self.download_dir.resolve() in path.parents and path.is_file():
                        path.unlink()
                        log.debug("Removed temporary file for job=%s", job.id)
                except OSError:
                    log.warning("Could not remove temporary file for job %s", job.id)
