from __future__ import annotations

import inspect
import logging
import sqlite3
from pathlib import Path
from typing import Any


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
    """Rubpy user-account sender using the Walrus upload-then-send flow.

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

        # Rubpy 7.3.5 logs the entire stored SQLite session at INFO in connect().
        # Give it an isolated WARNING-only logger so auth/private-key values cannot
        # leak into the worker's INFO logs.
        logger = logging.getLogger(f"telerubik.rubpy.{id(self)}")
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.WARNING)
        logger.propagate = False

        self.client = Client(name=self.session_name, logger=logger)
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
        # Do not mutate library-owned objects.
        return dict(candidate)

    async def send_document(self, path: Path | None, caption: str) -> Any:
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        send_method = getattr(self.client, "send_message", None)
        if send_method is None:
            raise RuntimeError("Installed Rubpy Client lacks send_message")
        if path is None:
            if not caption.strip():
                raise RuntimeError("Text-only Telegram post has no text")
            result = await _resolve(
                send_method(object_guid=self.destination_guid, text=caption.strip())
            )
        else:
            path = Path(path)
            if not path.is_file():
                raise RuntimeError("Upload source file does not exist")
            upload_method = getattr(self.client, "upload", None)
            if upload_method is None:
                raise RuntimeError("Installed Rubpy Client lacks upload; refusing unsafe fallback")
            uploaded = await _resolve(upload_method(str(path), file_name=path.name))
            file_inline = self._file_inline(uploaded)
            result = await _resolve(
                send_method(
                    object_guid=self.destination_guid,
                    text=caption.strip() or None,
                    file_inline=file_inline,
                )
            )
        if result is None:
            raise RuntimeError("Rubpy returned no result for send_message")
        status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
        if status is not None and str(status).strip().upper() not in {"OK", "SUCCESS", "SUCCESSFUL"}:
            raise RuntimeError("Rubika rejected the message (non-success response status)")
        return result

    async def close(self) -> None:
        if self.client is None:
            return
        client, self.client = self.client, None
        for name in ("disconnect", "close", "stop"):
            method = getattr(client, name, None)
            if method is not None:
                await _resolve(method())
                break
