from __future__ import annotations

import asyncio
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


async def _register_rubpy_device(client: Any) -> None:
    """Register this ephemeral worker device before file-upload API calls.

    Rubpy's normal start() flow registers a device only during interactive
    sign-in. A restored session can pass get_me() while requestSendFile still
    rejects an unregistered runner device, so hosted workers must register it
    explicitly on each fresh runner.
    """
    register = getattr(client, "register_device", None)
    if not callable(register):
        raise RuntimeError("Installed Rubpy Client does not support register_device")
    await _resolve(register())


async def _enable_verified_rubpy_transport(client: Any) -> None:
    """Replace Rubpy 7.3.5's TLS-verification-disabled session before API use."""
    import aiohttp

    connection = getattr(client, "connection", None)
    old_session = getattr(connection, "session", None)
    headers = getattr(connection, "headers", None)
    if old_session is None or not isinstance(headers, dict):
        raise RuntimeError("Rubpy did not expose the expected network session")

    await _resolve(old_session.close())
    connection.session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=True, limit=100),
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=client.timeout),
    )


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
            # Match the established Rubpy Client lifecycle used by Walrus and TG2RUB.
            # start() restores the account session, initializes the network client,
            # validates the account and performs registration only when Rubika asks.
            await _resolve(self.client.start())
            await _enable_verified_rubpy_transport(self.client)
            if not self.client.auth or not self.client.private_key:
                raise RuntimeError("Rubpy session does not contain authentication material")

            from rubpy.crypto import Crypto
            from Crypto.PublicKey import RSA
            from Crypto.Signature import pkcs1_15

            self.client.decode_auth = Crypto.decode_auth(self.client.auth)
            # Rubpy 7.3.5 does not derive this for a restored session.
            self.client.key = Crypto.passphrase(self.client.auth)
            self.client.import_key = pkcs1_15.new(
                RSA.import_key(self.client.private_key.encode())
            )
            if not getattr(self.client, "guid", None):
                raise RuntimeError("Rubika did not return the authenticated account GUID")
            # The restored session is validated before any upload. Device registration\n            # is attempted lazily only if Rubika explicitly rejects the upload as NOT_REGISTERED.\n        except Exception as exc:
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
        if path.stat().st_size <= 0:
            raise RuntimeError("Empty files cannot be uploaded through Rubpy 7.3.5")
        upload_method = getattr(self.client, "upload", None)
        send_method = getattr(self.client, "send_message", None)
        if upload_method is None or send_method is None:
            raise RuntimeError("Installed Rubpy Client lacks upload/send_message; refusing unsafe fallback")
        try:
            uploaded = await _resolve(upload_method(str(path), file_name=path.name))
        except Exception as exc:
            # Match Rubpy's own sign-in recovery path, but do not register a new
            # device on every run. Only do it when requestSendFile explicitly says
            # NOT_REGISTERED, then retry that upload exactly once.
            if type(exc).__name__ != "NotRegistered":
                raise
            register = getattr(self.client, "register_device", None)
            if not callable(register):
                raise RuntimeError("Rubika rejected the upload as NOT_REGISTERED and this Rubpy client cannot register") from None
            await _resolve(register())
            uploaded = await _resolve(upload_method(str(path), file_name=path.name))

        base_inline = self._file_inline(uploaded)
        suffix = path.suffix.lower()
        preferred_type = (
            "Video" if suffix in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv"}
            else "Image" if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
            else "Music" if suffix in {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
            else "File"
        )
        variants = [preferred_type] + (["File"] if preferred_type != "File" else [])
        last_error: Exception | None = None
        for inline_type in variants:
            file_inline = dict(base_inline)
            file_inline.update({
                "type": inline_type,
                "time": 1,
                "width": 200,
                "height": 200,
                "music_performer": "",
                "is_spoil": False,
            })
            for attempt in range(1, 4):
                try:
                    result = await _resolve(
                        send_method(
                            object_guid=self.destination_guid,
                            text=caption.strip() or None,
                            file_inline=file_inline,
                        )
                    )
                    self._validate_send_result(result)
                    return result
                except Exception as exc:
                    last_error = exc
                    error_text = f"{type(exc).__name__} {exc}".lower()
                    transient = any(token in error_text for token in (
                        "timeout", "timed out", "connection reset", "connection aborted",
                        "temporarily unavailable", "service unavailable", "bad gateway",
                        "internal_problem", "error_try_again", "500", "502", "503", "504",
                    ))
                    if transient and attempt < 3:
                        await asyncio.sleep(2 * attempt)
                        continue
                    break
        raise last_error if last_error else RuntimeError("Rubika message finalization failed")

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
        if (status is None) != (status_det is None):
            raise RuntimeError("Rubika response contained an incomplete success status pair")
        if status is None and status_det is None:
            # Rubpy 7.3.5 Builder validates the outer status/status_det pair,
            # then returns Update(result["data"]); that Update intentionally
            # contains the message payload, not the outer status fields.
            payload = getattr(result, "original_update", None)
            message_id = field(result, "message_id")
            if isinstance(payload, dict) and message_id:
                return
            raise RuntimeError("Rubika response did not contain a verifiable success result")

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
