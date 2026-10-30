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



def verify_cached_file(path: str | Path, expected_size: int | None) -> bool:
    """Return true only for a size-matching cache entry with a matching SHA-256 sidecar."""
    cached = Path(path)
    if expected_size is None or expected_size < 0 or not cached.is_file():
        return False
    sidecar = cached.with_name(cached.name + ".sha256")
    try:
        expected_hash = sidecar.read_text(encoding="ascii").strip()
        return (
            cached.stat().st_size == expected_size
            and len(expected_hash) == 64
            and all(ch in "0123456789abcdef" for ch in expected_hash)
            and sha256_file(cached) == expected_hash
        )
    except (OSError, UnicodeError):
        return False


def write_cache_sidecar(path: str | Path) -> str:
    """Atomically persist the SHA-256 of a completed download beside the media."""
    cached = Path(path)
    sidecar = cached.with_name(cached.name + ".sha256")
    temporary = sidecar.with_name(sidecar.name + ".tmp")
    digest = sha256_file(cached)
    try:
        temporary.write_text(digest + "\n", encoding="ascii")
        temporary.replace(sidecar)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest
