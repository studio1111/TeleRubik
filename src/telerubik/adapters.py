from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .chunks import LARGE_FILE_PART_SIZE, LARGE_FILE_SPLIT_THRESHOLD, iter_zip_parts


async def _resolve(value: Any) -> Any:
    """Accept both awaitable and synchronous Rubpy implementations."""
    return await value if inspect.isawaitable(value) else value


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
            # Use the established Rubpy start() lifecycle from Walrus and TG2RUB,
            # but establish certificate-verified transport before its first API call.
            await _resolve(self.client.connect())
            await _enable_verified_rubpy_transport(self.client)
            if not self.client.auth or not self.client.private_key:
                raise RuntimeError("Rubpy session does not contain authentication material")

            from rubpy.crypto import Crypto
            self.client.key = Crypto.passphrase(self.client.auth)
            # start() validates the restored account on this already-connected
            # client and fills decode_auth, import_key and guid.
            await _resolve(self.client.start())
            if not getattr(self.client, "guid", None):
                raise RuntimeError("Rubika did not return the authenticated account GUID")
            # Device registration is attempted lazily only after an explicit NOT_REGISTERED response.
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

    async def _send_zip_chunked_document(self, path: Path, caption: str) -> dict[str, Any]:
        """Use TG2RUB's >1.5 GiB / 500 MiB ZIP-part strategy without duplicating the full file."""
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        total_parts = max(1, (path.stat().st_size + LARGE_FILE_PART_SIZE - 1) // LARGE_FILE_PART_SIZE)
        sent_parts = 0
        manifest_data: dict[str, Any] | None = None

        with tempfile.TemporaryDirectory(prefix="telerubik-zip-parts-") as temporary:
            part_dir = Path(temporary)
            for item in iter_zip_parts(path, part_dir, LARGE_FILE_PART_SIZE):
                if "manifest" in item:
                    manifest_data = item["manifest"]
                    manifest_path = part_dir / f"{path.name}.manifest.json"
                    with manifest_path.open("x", encoding="utf-8") as output:
                        json.dump(manifest_data, output, ensure_ascii=False, indent=2)
                        output.write("\\n")
                        output.flush()
                        os.fsync(output.fileno())
                    manifest_caption = (
                        f"ZIP PART MANIFEST: {path.name}\\n"
                        f"Parts: {total_parts} | Original bytes: {manifest_data['total_bytes']}\\n"
                        f"Full-file SHA-256: {manifest_data['sha256']}\\n"
                        "Download every ZIP part and this manifest into one directory. "
                        "Run the built-in reassembly helper; it reads the ZIP members in order and verifies SHA-256."
                    )
                    await self._send_single_document(manifest_path, manifest_caption)
                    break

                archive_path = Path(item["archive_path"])
                index = int(item["index"])
                part_caption = (
                    (caption.strip() + "\\n\\n") if index == 1 and caption.strip() else ""
                ) + (
                    f"{path.name} | PART {index:03d}/{total_parts:03d} (ZIP)\\n"
                    f"Original bytes: {item['size_bytes']} | SHA-256: {item['sha256']}\\n"
                    "Extract this ZIP; join the numbered .part files in order."
                )
                await self._send_single_document(archive_path, part_caption)
                archive_path.unlink(missing_ok=True)
                sent_parts += 1

        if manifest_data is None or sent_parts != total_parts:
            raise RuntimeError("Large-file ZIP transfer did not produce all parts and a manifest")
        return {
            "status": "OK",
            "chunked": True,
            "format": "zip-parts",
            "parts": sent_parts,
            "sha256": manifest_data["sha256"],
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
        if path.stat().st_size > LARGE_FILE_SPLIT_THRESHOLD:
            return await self._send_zip_chunked_document(path, caption)
        # Walrus-style upload-then-send keeps ordinary files as one Rubika document.
        return await self._send_single_document(path, caption)

    async def close(self) -> None:
        if self.client is None:
            return
        client, self.client = self.client, None
        for name in ("disconnect", "close", "stop"):
            method = getattr(client, name, None)
            if method is not None:
                await _resolve(method())
                break
