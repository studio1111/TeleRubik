"""Application-level file chunking with integrity-checked reassembly.

This is NOT Rubika's native resumable upload protocol. It creates numbered
local parts and a manifest so a compatible receiver can reconstruct the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ChunkingError(ValueError):
    """Raised when a chunk manifest or part fails validation."""



def split_file(source: str | Path, parts_dir: str | Path, *, chunk_size: int) -> dict[str, Any]:
    """Split source into numbered parts and write a JSON manifest."""
    source_path = Path(source)
    target_dir = Path(parts_dir)
    if not source_path.is_file():
        raise ChunkingError("source must be an existing regular file")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ChunkingError("chunk_size must be a positive integer")

    source_resolved = source_path.resolve()
    target_resolved = target_dir.resolve()
    if source_resolved == target_resolved or target_resolved in source_resolved.parents:
        raise ChunkingError("source must not be inside the parts directory")
    target_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    total_size = 0
    overall = hashlib.sha256()
    with source_path.open("rb") as src:
        index = 1
        while True:
            data = src.read(chunk_size)
            if not data and index > 1:
                break
            part_name = f"part-{index:06d}.bin"
            part_path = target_dir / part_name
            # Write each part atomically to avoid leaving a partial part behind.
            fd, temporary = tempfile.mkstemp(prefix=".part-", dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, part_path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
            overall.update(data)
            entries.append({
                "index": index,
                "name": part_name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
            total_size += len(data)
            index += 1
            if not data:
                break

    manifest: dict[str, Any] = {
        "format": "telerubik-chunks-v1",
        "original_name": source_path.name,
        "size": total_size,
        "sha256": overall.hexdigest(),
        "chunk_size": chunk_size,
        "parts": len(entries),
        "parts_directory": str(target_dir.resolve()),
        "part_files": entries,
    }
    manifest_path = target_dir / "manifest.json"
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=target_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return manifest


def reassemble_file(manifest: dict[str, Any] | str | Path, destination: str | Path) -> Path:
    """Reassemble numbered parts only after validating every part and final hash."""
    if isinstance(manifest, (str, Path)):
        manifest_path = Path(manifest)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChunkingError("manifest cannot be read as valid JSON") from exc
        base_dir = manifest_path.parent.resolve()
    else:
        data = manifest
        try:
            base_dir = Path(data["parts_directory"]).resolve()
        except (KeyError, TypeError, OSError) as exc:
            raise ChunkingError("manifest has no valid parts directory") from exc

    if not isinstance(data, dict) or data.get("format") != "telerubik-chunks-v1":
        raise ChunkingError("unsupported manifest format")
    entries = data.get("part_files")
    if not isinstance(entries, list) or not entries:
        raise ChunkingError("manifest must contain at least one part")
    if isinstance(data.get("parts"), bool) or not isinstance(data.get("parts"), int) or data["parts"] != len(entries):
        raise ChunkingError("manifest part count does not match entries")
    expected_size = data.get("size")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise ChunkingError("manifest size must be a non-negative integer")
    expected_hash = data.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        raise ChunkingError("manifest SHA-256 is invalid")

    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reassemble-", dir=output_path.parent)
    digest = hashlib.sha256()
    total_size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            for expected_index, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict) or entry.get("index") != expected_index:
                    raise ChunkingError("parts must be in contiguous numbered order")
                name = entry.get("name")
                if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
                    raise ChunkingError("unsafe part filename")
                part_path = (base_dir / name).resolve()
                if part_path.parent != base_dir:
                    raise ChunkingError("part path escapes parts directory")
                try:
                    part_bytes = part_path.read_bytes()
                except OSError as exc:
                    raise ChunkingError(f"part {expected_index} is missing or unreadable") from exc
                expected_part_size = entry.get("size")
                if isinstance(expected_part_size, bool) or not isinstance(expected_part_size, int) or expected_part_size < 0:
                    raise ChunkingError(f"part {expected_index} size metadata is invalid")
                expected_part_hash = entry.get("sha256")
                if not isinstance(expected_part_hash, str) or len(expected_part_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_part_hash):
                    raise ChunkingError(f"part {expected_index} hash metadata is invalid")
                if len(part_bytes) != expected_part_size:
                    raise ChunkingError(f"part {expected_index} size mismatch")
                if hashlib.sha256(part_bytes).hexdigest() != expected_part_hash:
                    raise ChunkingError(f"part {expected_index} hash mismatch")
                out.write(part_bytes)
                digest.update(part_bytes)
                total_size += len(part_bytes)
            out.flush()
            os.fsync(out.fileno())
        if total_size != expected_size:
            raise ChunkingError("reassembled file size mismatch")
        if digest.hexdigest() != expected_hash:
            raise ChunkingError("reassembled file SHA-256 mismatch")
        os.replace(temporary, output_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return output_path
