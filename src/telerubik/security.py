from __future__ import annotations
import hashlib
import re
from pathlib import Path


def safe_filename(value: str | None, fallback: str = "telegram-file") -> str:
    candidate = (value or "").replace("\\", "/").split("/")[-1]
    candidate = "".join(ch for ch in candidate if ch.isprintable())
    candidate = re.sub(r"[<>:\"|?*]", "_", candidate).strip(" .")
    if candidate in {"", ".", ".."}:
        candidate = fallback
    stem = candidate.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        candidate = "_" + candidate
    return candidate[:180] or fallback


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file_size(size: int | None, max_bytes: int) -> bool:
    return size is not None and size >= 0 and max_bytes > 0 and size <= max_bytes
