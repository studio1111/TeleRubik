from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from .adapters import RubikaSender
from .config import Settings
from .security import safe_filename, validate_file_size

log = logging.getLogger(__name__)


async def transfer_one(
    settings: Settings,
    source_chat: str,
    message_id: int,
    expected_filename: str,
) -> None:
    """Fetch, validate and send exactly one Telegram message; never scan a chat."""
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    expected = safe_filename(expected_filename)
    if expected != expected_filename or expected in {"", ".", ".."}:
        raise ValueError("expected_filename must be a plain safe filename")
    if settings.dry_run:
        raise RuntimeError("transfer-one requires TELERUBIK_DRY_RUN=false")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("Telethon is missing; install the pinned project dependencies") from exc

    if not settings.telegram_session_string:
        raise RuntimeError("TELEGRAM_SESSION_STRING is required for remote single-message transfer")

    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    sender = RubikaSender(settings.rubika_session_name, settings.rubika_destination_guid)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        entity = await client.get_entity(_chat_ref(source_chat))
        message = await client.get_messages(entity, ids=message_id)
        if message is None or getattr(message, "id", None) != message_id:
            raise RuntimeError("The requested Telegram message was not found")
        if not getattr(message, "media", None):
            raise RuntimeError("The requested Telegram message does not contain media")

        actual_name = safe_filename(
            getattr(getattr(message, "file", None), "name", None)
        )
        if actual_name != expected:
            raise RuntimeError("Telegram filename does not match the approved filename")

        declared_size = getattr(getattr(message, "file", None), "size", None)
        if type(declared_size) is not int or not validate_file_size(
            declared_size, settings.max_file_size_bytes
        ):
            raise RuntimeError("Telegram media size is missing, invalid, or exceeds the configured limit")

        # Authenticate and validate the Rubika destination before downloading
        # a potentially multi-gigabyte Telegram file.
        await sender.start()

        with tempfile.TemporaryDirectory(prefix="telerubik-single-") as temporary:
            root = Path(temporary).resolve()
            target = root / expected
            result = await client.download_media(message, file=str(target))
            if not result:
                raise RuntimeError("Telegram download returned no file")
            downloaded = Path(result).resolve()
            if root not in downloaded.parents or not downloaded.is_file():
                raise RuntimeError("Telegram returned a path outside the isolated transfer directory")
            if downloaded.name != expected:
                raise RuntimeError("Downloaded filename changed unexpectedly")
            actual_size = downloaded.stat().st_size
            if actual_size != declared_size or not validate_file_size(
                actual_size, settings.max_file_size_bytes
            ):
                raise RuntimeError("Downloaded file size does not match Telegram metadata")

            caption = getattr(message, "message", "") or ""
            await sender.send_document(downloaded, caption)
            log.info(
                "Single-message transfer accepted: chat=%s message_id=%s filename=%s bytes=%s",
                source_chat, message_id, expected, actual_size,
            )
    finally:
        await sender.close()
        await client.disconnect()


def _chat_ref(value: str):
    return int(value) if value.lstrip("-").isdigit() else value
