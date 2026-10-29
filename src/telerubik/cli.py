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
    parser = argparse.ArgumentParser(prog="telerubik", description="Transfer authorized Telegram media to Rubika.")
    parser.add_argument("command", choices=("check", "status", "run"))
    return parser

def _chat_ref(value: str):
    """Telethon expects numeric chat IDs as integers, not username-like strings."""
    return int(value) if value.lstrip("-").isdigit() else value

async def _run(settings: Settings, queue: TransferQueue) -> None:
    try: from telethon import TelegramClient
    except ImportError as exc: raise RuntimeError("Telethon is missing; install requirements.txt") from exc
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(settings.telegram_session_path), settings.telegram_api_id, settings.telegram_api_hash)
    sender = RubikaSender(settings.rubika_bot_token, settings.rubika_destination_guid)
    await client.start()
    if not settings.dry_run: await sender.start()

    async def downloader(job, directory: Path):
        entity = await client.get_entity(_chat_ref(job.source_chat))
        message = await client.get_messages(entity, ids=job.message_id)
        if not message or not message.media: return None
        name = safe_filename(getattr(getattr(message, "file", None), "name", None) or job.filename)
        result = await client.download_media(message, file=str(directory / f"{job.id}-{name}"))
        return Path(result) if result else None

    async def upload(path: Path, caption: str): await sender.send_document(path, caption)
    engine = TransferEngine(queue, settings.download_dir, settings.max_file_size_bytes, settings.max_retries, settings.retry_base_seconds, settings.dry_run)
    try:
        while True:
            for chat in settings.telegram_source_chats:
                chat_ref = _chat_ref(chat)
                async for message in client.iter_messages(chat_ref, limit=settings.lookback_limit):
                    if not message.media or not getattr(message, "id", None): continue
                    if settings.allowed_user_ids and getattr(message, "sender_id", None) not in settings.allowed_user_ids: continue
                    size = getattr(getattr(message, "file", None), "size", None)
                    if size is not None and size > settings.max_file_size_bytes: continue
                    name = safe_filename(getattr(getattr(message, "file", None), "name", None) or f"telegram-{message.id}")
                    queue.enqueue(str(chat_ref), int(message.id), name, getattr(message, "message", "") or "", {"source_chat": str(chat_ref), "message_id": int(message.id)})
            while await engine.process_one(downloader, upload): pass
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
            for status, count in queue.stats().items(): print(f"{status}: {count}")
        else: asyncio.run(_run(settings, queue))
        return 0
    except (ConfigurationError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 2

if __name__ == "__main__": raise SystemExit(main())
