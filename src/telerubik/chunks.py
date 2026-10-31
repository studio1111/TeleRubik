from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any

DEFAULT_CHUNK_SIZE = 50 * 1024 * 1024
LARGE_FILE_SPLIT_THRESHOLD = 1536 * 1024 * 1024
LARGE_FILE_PART_SIZE = 500 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_file(source: str | Path, output_dir: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[str, Any]:
    """Split a file into ordered, independently verifiable parts and write a JSON manifest.

    Existing output files are never overwritten. The caller should use a fresh
    temporary directory for each transfer attempt.
    """
    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError("Source must be a regular file")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if source_path == destination or destination in source_path.parents:
        raise ValueError("Output directory must not contain or replace the source file")

    total_bytes = source_path.stat().st_size
    total_parts = max(1, (total_bytes + chunk_size - 1) // chunk_size)
    full_digest = hashlib.sha256()
    parts: list[dict[str, Any]] = []
    created: list[Path] = []
    try:
        with source_path.open("rb") as source_stream:
            for index in range(1, total_parts + 1):
                name = f"{source_path.name}.part{index:04d}-of-{total_parts:04d}"
                part_path = destination / name
                digest = hashlib.sha256()
                part_bytes = 0
                # Exclusive creation prevents accidentally clobbering old parts.
                with part_path.open("xb") as part_stream:
                    created.append(part_path)
                    remaining = chunk_size
                    while remaining:
                        block = source_stream.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        part_stream.write(block)
                        digest.update(block)
                        full_digest.update(block)
                        part_bytes += len(block)
                        remaining -= len(block)
                    part_stream.flush()
                    os.fsync(part_stream.fileno())
                parts.append({"index": index, "filename": name, "size_bytes": part_bytes, "sha256": digest.hexdigest()})

        manifest: dict[str, Any] = {
            "format": "telerubik-chunks-v1",
            "original_filename": source_path.name,
            "total_bytes": total_bytes,
            "chunk_size_bytes": chunk_size,
            "total_parts": total_parts,
            "sha256": full_digest.hexdigest(),
            "parts": parts,
        }
        manifest_path = destination / f"{source_path.name}.manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        created.append(manifest_path)
        if sum(part["size_bytes"] for part in parts) != total_bytes:
            raise OSError("Source changed while it was being split")
        return manifest
    except Exception:
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise



def iter_zip_parts(
    source: str | Path,
    output_dir: str | Path,
    part_size: int = LARGE_FILE_PART_SIZE,
):
    """Yield TG2RUB-style ZIP parts one at a time, then a verified manifest.

    Each archive uses ZIP_STORED (no expensive recompression) and contains one
    byte-range member. The caller can upload and delete each archive before the
    next one is generated, keeping peak disk usage bounded.
    """
    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError("Source must be a regular file")
    if type(part_size) is not int or part_size <= 0:
        raise ValueError("part_size must be a positive integer")
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if destination in source_path.parents or source_path == destination:
        raise ValueError("Output directory must not contain or replace the source file")

    before = source_path.stat()
    total_bytes = before.st_size
    total_parts = max(1, (total_bytes + part_size - 1) // part_size)
    full_digest = hashlib.sha256()
    parts: list[dict[str, Any]] = []
    bytes_read = 0

    try:
        with source_path.open("rb") as source_stream:
            for index in range(1, total_parts + 1):
                archive_name = f"{source_path.name}.part{index:03d}-of-{total_parts:03d}.zip"
                member_name = f"{source_path.name}.part{index:03d}"
                archive_path = destination / archive_name
                part_digest = hashlib.sha256()
                part_bytes = 0
                remaining = min(part_size, total_bytes - bytes_read)

                with zipfile.ZipFile(
                    archive_path, "x", compression=zipfile.ZIP_STORED, allowZip64=True
                ) as archive:
                    with archive.open(member_name, "w", force_zip64=True) as member:
                        while remaining:
                            block = source_stream.read(min(1024 * 1024, remaining))
                            if not block:
                                raise OSError("Source ended while ZIP part was being created")
                            member.write(block)
                            part_digest.update(block)
                            full_digest.update(block)
                            part_bytes += len(block)
                            bytes_read += len(block)
                            remaining -= len(block)

                item = {
                    "index": index,
                    "archive_filename": archive_name,
                    "member_filename": member_name,
                    "size_bytes": part_bytes,
                    "sha256": part_digest.hexdigest(),
                }
                parts.append(item)
                yield {"archive_path": archive_path, **item}

        after = source_path.stat()
        if (
            bytes_read != total_bytes
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or getattr(after, "st_ino", None) != getattr(before, "st_ino", None)
        ):
            raise OSError("Source file changed while ZIP parts were being created")

        yield {
            "manifest": {
                "format": "telerubik-zip-parts-v1",
                "original_filename": source_path.name,
                "total_bytes": total_bytes,
                "part_size_bytes": part_size,
                "total_parts": total_parts,
                "sha256": full_digest.hexdigest(),
                "parts": parts,
            }
        }
    finally:
        # The caller owns archives after each yield and deletes them after upload.
        # This final cleanup handles interruption while a generator is suspended.
        for item in destination.glob(f"{source_path.name}.part*-of-*.zip"):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass


def reassemble(manifest_path: str | Path, parts_dir: str | Path, output_path: str | Path) -> Path:
    """Reassemble manifest-listed parts after validating each part and the full hash."""
    manifest_file = Path(manifest_path).expanduser().resolve(strict=True)
    root = Path(parts_dir).expanduser().resolve(strict=True)
    target = Path(output_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Parts directory must be a directory")
    if target.exists():
        raise FileExistsError("Refusing to overwrite the reassembled output")
    with manifest_file.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    manifest_format = manifest.get("format")
    if manifest_format not in {"telerubik-chunks-v1", "telerubik-zip-parts-v1"} or not isinstance(manifest.get("parts"), list):
        raise ValueError("Unsupported or malformed chunk manifest")
    parts = manifest["parts"]
    if not parts or len(parts) != manifest.get("total_parts"):
        raise ValueError("Chunk manifest has an invalid part count")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".telerubik-partial")
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("xb") as output:
            for expected_index, item in enumerate(parts, start=1):
                if item.get("index") != expected_index:
                    raise ValueError("Unsafe or unordered filename in chunk manifest")
                if manifest_format == "telerubik-zip-parts-v1":
                    archive_name = item.get("archive_filename")
                    member_name = item.get("member_filename")
                    if (
                        not isinstance(archive_name, str) or Path(archive_name).name != archive_name
                        or not isinstance(member_name, str) or Path(member_name).name != member_name
                    ):
                        raise ValueError("Unsafe filename in ZIP part manifest")
                    archive_path = (root / archive_name).resolve(strict=True)
                    if archive_path.parent != root or not archive_path.is_file():
                        raise ValueError("ZIP part path escaped the parts directory")
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        info = archive.getinfo(member_name)
                        if info.file_size != item.get("size_bytes"):
                            raise ValueError(f"ZIP part {expected_index} has an invalid size")
                        with archive.open(info, "r") as source:
                            part_digest = hashlib.sha256()
                            part_total = 0
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                output.write(block)
                                digest.update(block)
                                part_digest.update(block)
                                total += len(block)
                                part_total += len(block)
                    if part_total != item.get("size_bytes") or part_digest.hexdigest() != item.get("sha256"):
                        raise ValueError(f"ZIP part {expected_index} failed integrity validation")
                else:
                    name = item.get("filename")
                    if not isinstance(name, str) or Path(name).name != name:
                        raise ValueError("Unsafe filename in chunk manifest")
                    part = (root / name).resolve(strict=True)
                    if part.parent != root or not part.is_file():
                        raise ValueError("Chunk path escaped the parts directory")
                    if part.stat().st_size != item.get("size_bytes") or _sha256_file(part) != item.get("sha256"):
                        raise ValueError(f"Chunk {expected_index} failed integrity validation")
                    with part.open("rb") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            output.write(block)
                            digest.update(block)
                            total += len(block)
            output.flush()
            os.fsync(output.fileno())
        if total != manifest.get("total_bytes") or digest.hexdigest() != manifest.get("sha256"):
            raise ValueError("Reassembled file failed full-size or SHA-256 validation")
        temporary.replace(target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
