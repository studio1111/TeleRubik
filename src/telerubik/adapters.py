from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .chunks import DEFAULT_CHUNK_SIZE


async def _resolve(value: Any) -> Any:
    """Accept both awaitable and synchronous Rubpy implementations."""
    return await value if inspect.isawaitable(value) else value


def _validate_authenticated_session(session_name: str) -> Path:
    """Check the local Rubpy SQLite session before starting a non-interactive worker."""
    name = Path(session_name).expanduser()
    path = name if name.suffix == ".rp" else Path(f"{name}.rp")
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("An authenticated Rubpy .rp session is required; create it locally first")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            check = connection.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise RuntimeError("Rubpy session database failed integrity validation")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"version", "session"}.issubset(tables):
                raise RuntimeError("Rubpy session database has an unexpected schema")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(session)")
            }
            required = {"phone", "auth", "guid", "agent", "private_key"}
            if not required.issubset(columns):
                raise RuntimeError("Rubpy session database is missing required fields")
            row = connection.execute(
                "SELECT phone, auth, guid, agent, private_key FROM session LIMIT 1"
            ).fetchone()
            if not row or not all(isinstance(value, str) and value for value in row):
                raise RuntimeError("Rubpy session is not authenticated")
        finally:
            connection.close()
    except sqlite3.Error:
        raise RuntimeError("Rubpy session database could not be validated") from None
    return path


class RubikaSender:
    """Rubpy user-account sender using upload-then-send and bounded disk usage.

    A pre-authenticated Rubpy session must be provisioned outside this process.
    The session name is a path prefix managed by Rubpy, not a bot token.
    """

    def __init__(self, session_name: str, destination_guid: str):
        self.session_name = session_name
        self.destination_guid = destination_guid
        self.client: Any = None

    async def start(self) -> None:
        try:
            from rubpy import Client
        except ImportError as exc:
            raise RuntimeError("Rubpy is missing; install the pinned project dependencies") from exc

        _validate_authenticated_session(self.session_name)

        # Rubpy 7.3.5 may log stored session fields at INFO during connect().
        # Isolate its logger so auth/private-key material cannot reach worker logs.
        logger = logging.getLogger(f"telerubik.rubpy.{id(self)}")
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.WARNING)
        logger.propagate = False

        self.client = Client(name=self.session_name, platform="Android", logger=logger)
        try:
            await _resolve(self.client.connect())
            if not self.client.auth or not self.client.private_key:
                raise RuntimeError("Rubpy session does not contain authentication material")

            from rubpy.crypto import Crypto
            from Crypto.PublicKey import RSA
            from Crypto.Signature import pkcs1_15

            self.client.decode_auth = Crypto.decode_auth(self.client.auth)
            self.client.import_key = pkcs1_15.new(
                RSA.import_key(self.client.private_key.encode())
            )
            me = await _resolve(self.client.get_me())
            guid = getattr(getattr(me, "user", None), "user_guid", None)
            if not guid:
                raise RuntimeError("Rubika did not return the authenticated account GUID")
            self.client.guid = guid
        except Exception as exc:
            client, self.client = self.client, None
            if client is not None:
                try:
                    await _resolve(client.disconnect())
                except Exception:
                    pass
            raise RuntimeError(
                f"Rubpy session verification failed ({type(exc).__name__}); "
                "authenticate the session locally again"
            ) from None

    @staticmethod
    def _file_inline(uploaded: Any) -> dict[str, Any]:
        if isinstance(uploaded, dict):
            candidate = uploaded
        else:
            candidate = getattr(uploaded, "to_dict", None)
            if callable(candidate):
                candidate = candidate()
            if not isinstance(candidate, dict):
                candidate = getattr(uploaded, "__dict__", None)
        if not isinstance(candidate, dict) or not candidate:
            raise RuntimeError("Rubpy upload returned no usable file metadata")
        return dict(candidate)

    async def _send_single_document(self, path: Path, caption: str) -> Any:
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        path = Path(path)
        if not path.is_file():
            raise RuntimeError("Upload source file does not exist")
        upload_method = getattr(self.client, "upload", None)
        send_method = getattr(self.client, "send_message", None)
        if upload_method is None or send_method is None:
            raise RuntimeError("Installed Rubpy Client lacks upload/send_message; refusing unsafe fallback")
        uploaded = await _resolve(upload_method(str(path), file_name=path.name))
        file_inline = self._file_inline(uploaded)
        result = await _resolve(
            send_method(
                object_guid=self.destination_guid,
                text=caption.strip() or None,
                file_inline=file_inline,
            )
        )
        self._validate_send_result(result)
        return result

    @staticmethod
    def _validate_send_result(result: Any) -> None:
        if result is None:
            raise RuntimeError("Rubpy returned no result for send_message")
        def field(value: Any, name: str) -> Any:
            return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

        status = field(result, "status")
        status_det = field(result, "status_det")
        if status is not None and str(status).strip().upper() not in {"OK", "SUCCESS", "SUCCESSFUL"}:
            raise RuntimeError("Rubika rejected the message (non-success response status)")
        if status_det is not None and str(status_det).strip().upper() not in {"OK", "SUCCESS", "SUCCESSFUL"}:
            raise RuntimeError("Rubika rejected the message (non-success response detail)")
        if field(result, "error"):
            raise RuntimeError("Rubika returned an error for the message")
        if status is None and status_det is None:
            raise RuntimeError("Rubika response did not contain an explicit success status")

    async def _send_chunked_document(self, path: Path, caption: str) -> dict[str, Any]:
        """Upload one bounded-size part at a time, then publish its integrity manifest.

        This avoids materializing a second full-size copy of a large video on disk.
        Parts remain sequential to preserve deterministic order and avoid flooding
        the destination with concurrent uploads.
        """
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        before = path.stat()
        total_bytes = before.st_size
        total_parts = max(1, math.ceil(total_bytes / DEFAULT_CHUNK_SIZE))
        full_digest = hashlib.sha256()
        parts: list[dict[str, Any]] = []
        bytes_read = 0

        with tempfile.TemporaryDirectory(prefix="telerubik-chunks-") as temporary:
            chunk_dir = Path(temporary)
            with path.open("rb") as source:
                for index in range(1, total_parts + 1):
                    part_name = f"{path.name}.part{index:04d}-of-{total_parts:04d}"
                    part_path = chunk_dir / part_name
                    part_digest = hashlib.sha256()
                    part_bytes = 0
                    remaining = DEFAULT_CHUNK_SIZE

                    with part_path.open("xb") as output:
                        while remaining:
                            block = source.read(min(1024 * 1024, remaining))
                            if not block:
                                break
                            output.write(block)
                            part_digest.update(block)
                            full_digest.update(block)
                            part_bytes += len(block)
                            bytes_read += len(block)
                            remaining -= len(block)
                        output.flush()
                        os.fsync(output.fileno())

                    expected_part_size = min(DEFAULT_CHUNK_SIZE, total_bytes - (index - 1) * DEFAULT_CHUNK_SIZE)
                    if part_bytes != expected_part_size:
                        raise RuntimeError("Source file changed or ended during chunk preparation")

                    parts.append({
                        "index": index,
                        "filename": part_name,
                        "size_bytes": part_bytes,
                        "sha256": part_digest.hexdigest(),
                    })
                    part_caption = (
                        (caption.strip() + "\n\n") if index == 1 and caption.strip() else ""
                    ) + (
                        f"{path.name} | PART {index:04d}/{total_parts:04d}\n"
                        f"Bytes: {part_bytes} | SHA-256: {part_digest.hexdigest()}\n"
                        "Join parts in ascending order; verify with the manifest."
                    )
                    await self._send_single_document(part_path, part_caption)
                    # Free the part before producing the next one.
                    part_path.unlink()

                if source.read(1):
                    raise RuntimeError("Source file grew during chunk transfer")

            after = path.stat()
            if (
                bytes_read != total_bytes
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or getattr(after, "st_ino", None) != getattr(before, "st_ino", None)
            ):
                raise RuntimeError("Source file changed during chunk transfer")

            manifest = {
                "format": "telerubik-chunks-v1",
                "original_filename": path.name,
                "total_bytes": total_bytes,
                "chunk_size_bytes": DEFAULT_CHUNK_SIZE,
                "total_parts": total_parts,
                "sha256": full_digest.hexdigest(),
                "parts": parts,
            }
            manifest_path = chunk_dir / f"{path.name}.manifest.json"
            with manifest_path.open("x", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())

            manifest_caption = (
                f"CHUNK MANIFEST: {path.name}\n"
                f"Parts: {total_parts} | Total bytes: {total_bytes}\n"
                f"Full-file SHA-256: {full_digest.hexdigest()}\n"
                "Download all numbered parts and this JSON manifest, then reassemble "
                "in order and verify SHA-256."
            )
            await self._send_single_document(manifest_path, manifest_caption)

        return {
            "status": "OK",
            "chunked": True,
            "parts": total_parts,
            "sha256": full_digest.hexdigest(),
        }

    async def send_document(self, path: Path | None, caption: str) -> Any:
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        if path is None:
            if not caption.strip():
                raise RuntimeError("Text-only Telegram post has no text")
            result = await _resolve(
                self.client.send_message(object_guid=self.destination_guid, text=caption.strip())
            )
            self._validate_send_result(result)
            return result

        path = Path(path)
        if not path.is_file():
            raise RuntimeError("Upload source file does not exist")
        if path.stat().st_size <= DEFAULT_CHUNK_SIZE:
            return await self._send_single_document(path, caption)
        return await self._send_chunked_document(path, caption)

    async def close(self) -> None:
        if self.client is None:
            return
        client, self.client = self.client, None
        for name in ("disconnect", "close", "stop"):
            method = getattr(client, name, None)
            if method is not None:
                await _resolve(method())
                break
