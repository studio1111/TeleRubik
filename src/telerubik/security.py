from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
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


def cache_sidecar_path(path: str | Path) -> Path:
    return Path(str(Path(path)) + ".sha256.json")


def write_cache_sidecar(path: str | Path) -> Path:
    target = Path(path)
    record = {"size": target.stat().st_size, "sha256": sha256_file(target)}
    sidecar = cache_sidecar_path(target)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{sidecar.name}.", dir=sidecar.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return sidecar


def verify_cached_file(path: str | Path, expected_size: int | None = None) -> bool:
    target = Path(path)
    sidecar = cache_sidecar_path(target)
    try:
        if not target.is_file() or not sidecar.is_file():
            return False
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        actual_size = target.stat().st_size
        if not isinstance(record, dict) or type(record.get("size")) is not int:
            return False
        if not isinstance(record.get("sha256"), str):
            return False
        if record["size"] != actual_size:
            return False
        if expected_size is not None and actual_size != expected_size:
            return False
        return record["sha256"] == sha256_file(target)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
