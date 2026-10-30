from __future__ import annotations

import asyncio
import csv
import json
import os
import re
from datetime import timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

QUALITY_RE = re.compile(r"(?<![0-9])(2160|1080|720|480|360)p(?![0-9])", re.IGNORECASE)


def classify_quality(filename: str) -> str:
    match = QUALITY_RE.search(filename)
    return f"{match.group(1)}p" if match else "unknown"


def message_record(message) -> dict | None:
    file_info = getattr(message, "file", None)
    if not file_info:
        return None
    filename = getattr(file_info, "name", None) or f"telegram-{message.id}"
    size = getattr(file_info, "size", None)
    date = getattr(message, "date", None)
    mime = getattr(file_info, "mime_type", None)
    return {
        "source": "@Animehighbo",
        "message_id": int(message.id),
        "date_utc": date.astimezone(timezone.utc).isoformat() if date else "",
        "filename": str(filename),
        "size_bytes": int(size) if size is not None else None,
        "size_mib": round(int(size) / (1024 * 1024), 2) if size is not None else None,
        "mime_type": str(mime or ""),
        "quality": classify_quality(str(filename)),
    }


async def collect() -> list[dict]:
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not (api_id and api_hash and session):
        raise RuntimeError("Missing one or more Telegram Actions secrets: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING")
    limit = int(os.environ.get("INVENTORY_MESSAGE_LIMIT", "2000"))
    if not 1 <= limit <= 10000:
        raise RuntimeError("INVENTORY_MESSAGE_LIMIT must be between 1 and 10000")
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.start()
        entity = await client.get_entity("@Animehighbo")
        records = []
        async for message in client.iter_messages(entity, limit=limit):
            row = message_record(message)
            if row:
                records.append(row)
        records.sort(key=lambda item: (item["date_utc"], item["message_id"]), reverse=True)
        return records
    finally:
        await client.disconnect()


def write_reports(records: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "telegram-file-inventory.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    columns = ["source", "message_id", "date_utc", "filename", "size_bytes", "size_mib", "mime_type", "quality"]
    with (output_dir / "telegram-file-inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)
    large_1080 = [
        row for row in records
        if row["quality"] == "1080p" and (row["size_bytes"] or 0) > 100 * 1024 * 1024
    ]
    (output_dir / "large-1080p-candidates.json").write_text(
        json.dumps(large_1080, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Inventory complete: {len(records)} media files; {len(large_1080)} 1080p files above 100 MiB.")
    for row in large_1080:
        print(f"candidate message_id={row['message_id']} size_mib={row['size_mib']} filename={row['filename']}")


async def main() -> None:
    records = await collect()
    write_reports(records, Path("inventory"))


if __name__ == "__main__":
    asyncio.run(main())
