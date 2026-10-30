from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import zipfile
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from rubpy.bot import BotClient

SOURCE = "@Animehighbo"
MESSAGE_ID = 1707
DESTINATION = "c0EHxkZ09df743e3bb77763624a65f2c"
WORK = Path("transfer-test")
REPORT = Path("transfer-test-report.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_report(data: dict) -> None:
    REPORT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def main() -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ["TELEGRAM_SESSION_STRING"]
    bot_token = os.environ["RUBIKA_BOT_TOKEN"]
    WORK.mkdir(parents=True, exist_ok=True)
    report = {
        "source": SOURCE,
        "source_message_id": MESSAGE_ID,
        "destination_label": "TeleRubik File (private)",
        "destination_guid_suffix": DESTINATION[-6:],
        "single_file": {"status": "not_started"},
        "zip_file": {"status": "not_started"},
        "chunked": {"status": "not_supported_unverified", "sent": False},
    }
    tg = TelegramClient(StringSession(session), api_id, api_hash)
    bot = BotClient(token=bot_token)
    try:
        await tg.start()
        entity = await tg.get_entity(SOURCE)
        message = await tg.get_messages(entity, ids=MESSAGE_ID)
        if not message or not message.media or not getattr(message, "file", None):
            raise RuntimeError("Source message is missing or has no downloadable media.")
        filename = message.file.name or f"telegram-{MESSAGE_ID}.bin"
        source_size = int(message.file.size or 0)
        if source_size <= 100_000_000:
            raise RuntimeError(f"Selected file is not above 100 MB: {source_size} bytes.")
        report["source_filename"] = filename
        report["source_size_bytes"] = source_size
        source_path = WORK / Path(filename).name
        downloaded = await tg.download_media(message, file=str(source_path))
        if not downloaded:
            raise RuntimeError("Telegram media download returned no file.")
        source_path = Path(downloaded)
        actual_size = source_path.stat().st_size
        source_hash = sha256(source_path)
        report["download"] = {"status": "success", "size_bytes": actual_size, "sha256": source_hash}
        if actual_size != source_size:
            raise RuntimeError(f"Downloaded size mismatch: expected {source_size}, got {actual_size}.")

        async def send(path: Path, caption: str) -> None:
            method = getattr(bot, "send_file", None)
            if method is None:
                raise RuntimeError("rubpy BotClient.send_file is unavailable.")
            result = method(DESTINATION, file=str(path), text=caption, file_name=path.name, type="File")
            if inspect.isawaitable(result):
                result = await result
            report_key = "single_file" if path == source_path else "zip_file"
            report[report_key] = {
                "status": "success",
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "response_type": type(result).__name__,
            }

        try:
            await send(source_path, f"TeleRubik test: single-file upload | source message {MESSAGE_ID}")
        except Exception as exc:
            report["single_file"] = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}

        zip_path = WORK / (source_path.name + ".zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                archive.write(source_path, arcname=source_path.name)
            with zipfile.ZipFile(zip_path, "r") as archive:
                bad_member = archive.testzip()
                if bad_member:
                    raise RuntimeError(f"ZIP integrity check failed: {bad_member}")
            with zipfile.ZipFile(zip_path, "r") as archive:
                extracted = archive.read(source_path.name)
            if hashlib.sha256(extracted).hexdigest() != source_hash:
                raise RuntimeError("ZIP round-trip SHA256 mismatch.")
            report["zip_creation"] = {
                "status": "success",
                "size_bytes": zip_path.stat().st_size,
                "compression_ratio": round(zip_path.stat().st_size / actual_size, 5),
                "integrity": "passed",
            }
            try:
                await send(zip_path, f"TeleRubik test: ZIP upload | source message {MESSAGE_ID}")
            except Exception as exc:
                report["zip_file"] = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
        except Exception as exc:
            report["zip_file"] = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}

        # Test byte-level split/reassembly locally only. This does NOT claim server-side chunked upload support.
        chunk_dir = WORK / "local-chunks"
        chunk_dir.mkdir()
        chunk_size = 8 * 1024 * 1024
        parts = []
        with source_path.open("rb") as src:
            index = 0
            while block := src.read(chunk_size):
                part = chunk_dir / f"part-{index:04d}.bin"
                part.write_bytes(block)
                parts.append(part)
                index += 1
        rebuilt = WORK / "reassembled.bin"
        with rebuilt.open("wb") as dst:
            for part in parts:
                with part.open("rb") as src:
                    shutil.copyfileobj(src, dst)
        reassembled_ok = rebuilt.stat().st_size == actual_size and sha256(rebuilt) == source_hash
        report["chunked"] = {
            "status": "server_protocol_not_documented_or_verified",
            "sent": False,
            "local_split_reassembly": "passed" if reassembled_ok else "failed",
            "chunk_size_bytes": chunk_size,
            "part_count": len(parts),
            "sha256_matches": reassembled_ok,
            "note": "No file parts were sent to Rubika because server-side chunk assembly support is unverified.",
        }
        write_report(report)
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        write_report(report)
        raise
    finally:
        for name in ("stop", "close", "disconnect"):
            closer = getattr(bot, name, None)
            if closer:
                result = closer()
                if inspect.isawaitable(result):
                    await result
                break
        await tg.disconnect()
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
