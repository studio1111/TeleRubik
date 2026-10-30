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
from .security import cache_sidecar_path, safe_filename, verify_cached_file, write_cache_sidecar

def _build_parser():
    parser = argparse.ArgumentParser(prog="telerubik", description="Transfer authorized Telegram media to Rubika.")
    parser.add_argument("command", choices=("check", "status", "run"))
    return parser

def _chat_ref(value: str):
    """Telethon expects numeric chat IDs as integers, not username-like strings."""
    return int(value) if value.lstrip("-").isdigit() else value

async def _run(settings: Settings, queue: TransferQueue) -> None:
    recovered = queue.recover_processing()
    if recovered:
        logging.warning("Recovered %s interrupted queue job(s)", recovered)
    try: from telethon import TelegramClient
    except ImportError as exc: raise RuntimeError("Telethon is missing; install requirements.txt") from exc
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(settings.telegram_session_path), settings.telegram_api_id, settings.telegram_api_hash)
    sender = RubikaSender(settings.rubika_session_name, settings.rubika_destination_guid)
    await client.start()
    if not settings.dry_run: await sender.start()

    async def downloader(job, directory: Path):
        entity = await client.get_entity(_chat_ref(job.source_chat))
        message = await client.get_messages(entity, ids=job.message_id)
        if not message or not message.media: return None
        name = safe_filename(getattr(getattr(message, "file", None), "name", None) or job.filename)
        target = (directory / f"{job.id}-{name}").resolve()
        root = directory.resolve()
        if root not in target.parents:
            raise RuntimeError("Resolved Telegram download path escaped the download directory")
        expected_size = job.payload.get("expected_size")
        if type(expected_size) is not int or expected_size < 0:
            expected_size = None
        if target.exists():
            if verify_cached_file(target, expected_size):
                logging.info("Reusing verified cached download for job %s", job.id)
                return target
            target.unlink(missing_ok=True)
            cache_sidecar_path(target).unlink(missing_ok=True)
        result = await client.download_media(message, file=str(target))
        if not result:
            return None
        downloaded_path = Path(result).resolve()
        if root not in downloaded_path.parents:
            raise RuntimeError("Telegram returned a path outside the download directory")
        if expected_size is not None and downloaded_path.stat().st_size != expected_size:
            downloaded_path.unlink(missing_ok=True)
            raise RuntimeError("Downloaded size does not match Telegram metadata")
        write_cache_sidecar(downloaded_path)
        return downloaded_path

    async def upload(path: Path, caption: str): await sender.send_document(path, caption)
    engine = TransferEngine(queue, settings.download_dir, settings.max_file_size_bytes, settings.max_retries, settings.retry_base_seconds, settings.dry_run)
    try:
        while True:
            for chat in settings.telegram_source_chats:
                chat_ref = _chat_ref(chat)
                async for message in client.iter_messages(chat_ref, limit=settings.lookback_limit):
                    if not getattr(message, "id", None): continue
                    caption = getattr(message, "message", "") or ""
                    if not message.media and not caption.strip(): continue
                    if settings.allowed_user_ids and getattr(message, "sender_id", None) not in settings.allowed_user_ids: continue
                    size = getattr(getattr(message, "file", None), "size", None)
                    if size is not None and size > settings.max_file_size_bytes: continue
                    name = safe_filename(
                        getattr(getattr(message, "file", None), "name", None)
                        or (f"telegram-{message.id}" if message.media else f"text-{message.id}")
                    )
                    queue.enqueue(
                        str(chat_ref),
                        int(message.id),
                        name,
                        caption,
                        {
                            "source_chat": str(chat_ref),
                            "message_id": int(message.id),
                            "expected_size": size if isinstance(size, int) and size >= 0 else None,
                            "text_only": not bool(message.media),
                        },
                    )
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
