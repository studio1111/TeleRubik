from __future__ import annotations
import argparse
import asyncio
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from .adapters import RubikaSender
from .config import ConfigurationError, Settings
from .engine import TransferEngine
from .queue import TransferQueue
from .security import safe_filename

def _build_parser():
    parser = argparse.ArgumentParser(prog="telerubik", description="Transfer authorized Telegram posts and media to Rubika.")
    parser.add_argument("command", choices=("check", "status", "run"))
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

async def _run(settings: Settings, queue: TransferQueue) -> None:
    recovered = queue.recover_processing()
    if recovered:
        logging.warning("Recovered %s interrupted queue job(s)", recovered)
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Telethon is missing; install requirements.txt") from exc
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(settings.telegram_session_path), settings.telegram_api_id, settings.telegram_api_hash)
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
        await client.start()
        if not settings.dry_run:
            await sender.start()
        while True:
            for chat in settings.telegram_source_chats:
                chat_ref = _chat_ref(chat)
                cursor = queue.get_cursor(str(chat_ref))
                if cursor:
                    # Catch every new message since the last completed scan, even after long downtime.
                    messages = [message async for message in client.iter_messages(chat_ref, min_id=cursor, reverse=True)]
                else:
                    # On first run, limit the initial import to the configured recent-history window.
                    messages = [message async for message in client.iter_messages(chat_ref, limit=settings.lookback_limit)]
                    messages.reverse()  # Initial Telethon batch is newest-first.
                highest_seen = cursor
                for message in messages:
                    message_id = getattr(message, "id", None)
                    if message_id:
                        highest_seen = max(highest_seen, int(message_id))
                    if not message_id:
                        continue
                    text = getattr(message, "message", "") or ""
                    kind = _message_kind(message)
                    if kind is None:
                        logging.debug("Skipping unsupported empty media chat=%s message_id=%s", chat_ref, message.id)
                        continue
                    if settings.allowed_user_ids and getattr(message, "sender_id", None) not in settings.allowed_user_ids:
                        continue
                    media_type = _media_type(message) if kind == "media" else "text"
                    size = getattr(getattr(message, "file", None), "size", None) if kind == "media" else None
                    if size is not None and size > settings.max_file_size_bytes:
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
                    queue.enqueue(str(chat_ref), int(message.id), name, text, payload)
                if highest_seen > cursor:
                    queue.set_cursor(str(chat_ref), highest_seen)
            if settings.dry_run:
                # Do not claim or complete durable jobs in preview mode. This keeps any queued work available for a later live run.
                logging.info("DRY RUN: scanned sources; queue status: %s", queue.stats())
            else:
                while await engine.process_one(downloader, upload):
                    pass
            await asyncio.sleep(settings.poll_seconds)
    finally:
        await sender.close()
        await client.disconnect()

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
            asyncio.run(_run(settings, queue))
        return 0
    except (ConfigurationError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
