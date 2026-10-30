from __future__ import annotations
import argparse
import asyncio
import logging
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
from .adapters import RubikaSender
from .config import ConfigurationError, Settings
from .engine import TransferEngine
from .queue import TransferQueue
from .security import safe_filename

def _build_parser():
    parser = argparse.ArgumentParser(prog="telerubik", description="Transfer authorized Telegram posts and media to Rubika.")
    parser.add_argument("command", choices=("check", "status", "run", "transfer-one"))
    parser.add_argument("--source-chat", help="Source chat for the one-message transfer")
    parser.add_argument("--message-id", type=int, help="Exact Telegram message ID for transfer-one")
    parser.add_argument("--expected-filename", help="Exact filename expected from the source message")
    return parser

def _chat_ref(value: str):
    """Telethon expects numeric chat IDs as integers, not username-like strings."""
    return int(value) if value.lstrip("-").isdigit() else value

def _message_kind(message) -> str | None:
    """Classify a source post without queueing unsupported media as a doomed file job."""
    has_media = bool(getattr(message, "media", None))
    file_info = getattr(message, "file", None)
    text = getattr(message, "message", "") or ""
    if has_media and file_info is not None:
        return "media"
    if text.strip():
        return "text"
    return None

def _media_type(message) -> str:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    file_info = getattr(message, "file", None)
    mime = (getattr(file_info, "mime_type", None) or "").lower()
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    return "file"

async def _iter_source_messages(client, chat_ref: str, cursor: int, lookback_limit: int):
    """Stream incremental history; only materialize the bounded first-run window."""
    if cursor:
        async for message in client.iter_messages(chat_ref, min_id=cursor, reverse=True):
            yield message
        return

    # Telethon's initial history is newest-first. The initial window is bounded,
    # so reversing this small batch preserves oldest-first queue order safely.
    messages = [message async for message in client.iter_messages(chat_ref, limit=lookback_limit)]
    for message in reversed(messages):
        yield message

async def _run(settings: Settings, queue: TransferQueue, single_job: tuple[str, int, str] | None = None) -> None:
    recovered = queue.recover_processing()
    if recovered:
        logging.warning("Recovered %s interrupted queue job(s)", recovered)
    logging.info(
        "Starting TeleRubik: mode=%s sources=%s poll_interval=%ss max_file_size=%s MiB queue=%s",
        "DRY RUN (sending disabled)" if settings.dry_run else "LIVE TRANSFER",
        len(settings.telegram_source_chats),
        settings.poll_seconds,
        settings.max_file_size_bytes // (1024 * 1024),
        queue.stats(),
    )
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Telethon is missing; install requirements.txt") from exc
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    if settings.telegram_session_string:
        from telethon.sessions import StringSession
        telegram_session = StringSession(settings.telegram_session_string)
        logging.info("Using ephemeral Telegram StringSession from environment.")
    else:
        telegram_session = str(settings.telegram_session_path)
    client = TelegramClient(telegram_session, settings.telegram_api_id, settings.telegram_api_hash)
    sender = RubikaSender(settings.rubika_bot_token, settings.rubika_destination_guid)

    async def downloader(job, directory: Path):
        if job.payload.get("kind") == "text":
            return None
        entity = await client.get_entity(_chat_ref(job.source_chat))
        message = await client.get_messages(entity, ids=job.message_id)
        if not message or not message.media or getattr(message, "file", None) is None:
            return None
        name = safe_filename(getattr(getattr(message, "file", None), "name", None) or job.filename)
        result = await client.download_media(message, file=str(directory / f"{job.id}-{name}"))
        return Path(result) if result else None

    async def upload(path: Path | None, caption: str, job):
        await sender.send_job(path, caption, job)

    engine = TransferEngine(queue, settings.download_dir, settings.max_file_size_bytes, settings.max_retries, settings.retry_base_seconds, settings.dry_run)
    try:
        logging.info("Connecting to Telegram...")
        await client.start()
        logging.info("Telegram connection established.")
        if not settings.dry_run:
            logging.info("Starting Rubika sender for configured destination.")
            await sender.start()
        else:
            logging.info("Dry run is enabled: no messages or media will be sent to Rubika.")

        if single_job is not None:
            source_chat, message_id, expected_filename = single_job
            if source_chat not in settings.telegram_source_chats:
                raise RuntimeError("The single-transfer source chat must be listed in TELEGRAM_SOURCE_CHATS")
            entity = await client.get_entity(_chat_ref(source_chat))
            message = await client.get_messages(entity, ids=message_id)
            if not message or not message.media or getattr(message, "file", None) is None:
                raise RuntimeError("The selected Telegram message does not contain downloadable media")
            actual_filename = safe_filename(getattr(message.file, "name", None) or f"telegram-{message_id}")
            if actual_filename != safe_filename(expected_filename):
                raise RuntimeError("The selected message filename does not match --expected-filename")
            size = getattr(message.file, "size", None)
            if size is None or size <= 0 or size > settings.max_file_size_bytes:
                raise RuntimeError("The selected file size is missing, invalid, or exceeds the configured limit")
            free_bytes = shutil.disk_usage(settings.download_dir).free
            required_free_bytes = size + 256 * 1024 * 1024
            if free_bytes < required_free_bytes:
                raise RuntimeError(
                    f"Insufficient free disk space for the selected file: need at least "
                    f"{required_free_bytes} bytes including safety reserve"
                )
            payload = {
                "source_chat": source_chat,
                "message_id": message_id,
                "kind": "media",
                "media_type": _media_type(message),
                "grouped_id": getattr(message, "grouped_id", None),
            }
            queue.enqueue(source_chat, message_id, actual_filename, getattr(message, "message", "") or "", payload)
            logging.info("Single-transfer scope locked to source=%s message_id=%s filename=%s bytes=%s",
                         source_chat, message_id, actual_filename, size)
            while await engine.process_one(downloader, upload, source_chat=source_chat, message_id=message_id):
                pass
            stats = queue.stats()
            logging.info("Single-transfer pass complete; queue status: %s", stats)
            _validate_single_transfer_result(stats, dry_run=settings.dry_run)
            return

        while True:
            for chat in settings.telegram_source_chats:
                chat_ref = _chat_ref(chat)
                cursor = queue.get_cursor(str(chat_ref))
                logging.info(
                    "Scanning source %s (%s).",
                    chat_ref,
                    "new messages after saved cursor" if cursor else f"initial history limit={settings.lookback_limit}",
                )
                highest_seen = cursor
                scanned = queued = duplicates = skipped = 0
                async for message in _iter_source_messages(client, chat_ref, cursor, settings.lookback_limit):
                    scanned += 1
                    message_id = getattr(message, "id", None)
                    if message_id:
                        highest_seen = max(highest_seen, int(message_id))
                    if not message_id:
                        skipped += 1
                        continue
                    text = getattr(message, "message", "") or ""
                    kind = _message_kind(message)
                    if kind is None:
                        skipped += 1
                        logging.debug("Skipping unsupported empty media chat=%s message_id=%s", chat_ref, message.id)
                        continue
                    if settings.allowed_user_ids and getattr(message, "sender_id", None) not in settings.allowed_user_ids:
                        skipped += 1
                        continue
                    media_type = _media_type(message) if kind == "media" else "text"
                    size = getattr(getattr(message, "file", None), "size", None) if kind == "media" else None
                    if size is not None and size > settings.max_file_size_bytes:
                        skipped += 1
                        logging.warning("Skipping oversized source message chat=%s message_id=%s size=%s", chat_ref, message.id, size)
                        continue
                    fallback = f"telegram-{message.id}"
                    name = safe_filename(getattr(getattr(message, "file", None), "name", None) or fallback)
                    payload = {
                        "source_chat": str(chat_ref),
                        "message_id": int(message.id),
                        "kind": kind,
                        "media_type": media_type,
                        "grouped_id": getattr(message, "grouped_id", None),
                    }
                    if queue.enqueue(str(chat_ref), int(message.id), name, text, payload):
                        queued += 1
                    else:
                        duplicates += 1
                if highest_seen > cursor:
                    queue.set_cursor(str(chat_ref), highest_seen)
                logging.info(
                    "Source scan complete chat=%s scanned=%s newly_queued=%s already_queued=%s skipped=%s cursor=%s",
                    chat_ref, scanned, queued, duplicates, skipped, highest_seen,
                )
            if settings.dry_run:
                # Do not claim or complete durable jobs in preview mode. This keeps any queued work available for a later live run.
                logging.info("DRY RUN: sending is disabled; queue status: %s", queue.stats())
            else:
                while await engine.process_one(downloader, upload):
                    pass
                logging.info("Transfer pass complete; queue status: %s", queue.stats())
            await asyncio.sleep(settings.poll_seconds)
    finally:
        await sender.close()
        await client.disconnect()

def _validate_single_transfer_result(stats: dict[str, int], dry_run: bool) -> None:
    """Fail closed unless the isolated transfer has the expected terminal state."""
    expected = (
        {"pending": 1, "processing": 0, "completed": 0, "failed": 0}
        if dry_run
        else {"pending": 0, "processing": 0, "completed": 1, "failed": 0}
    )
    actual = {key: int(stats.get(key, 0)) for key in expected}
    if actual != expected:
        raise RuntimeError(
            "Single-transfer did not reach its required state: "
            f"expected={expected}, actual={actual}. A green process exit is not proof of delivery."
        )

def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
        queue = TransferQueue(settings.state_db)
        if args.command == "check":
            print("Configuration valid. Secrets are not displayed.")
            print(f"Source chats configured: {len(settings.telegram_source_chats)}")
            print(f"Max file size: {settings.max_file_size_bytes // (1024 * 1024)} MiB")
            print(f"Dry run: {settings.dry_run}")
        elif args.command == "status":
            print("Queue status")
            for status, count in queue.stats().items():
                print(f"{status}: {count}")
        else:
            if args.command == "transfer-one":
                if not args.source_chat or not args.message_id or not args.expected_filename:
                    raise ConfigurationError("transfer-one requires --source-chat, --message-id, and --expected-filename")
                asyncio.run(_run(settings, queue, (args.source_chat, args.message_id, args.expected_filename)))
            else:
                asyncio.run(_run(settings, queue))
        return 0
    except (ConfigurationError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
