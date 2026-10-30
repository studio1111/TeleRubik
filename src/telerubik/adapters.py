from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any


async def _resolve(value: Any) -> Any:
    """Accept both awaitable and synchronous Rubpy implementations."""
    return await value if inspect.isawaitable(value) else value


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
        self.client = Client(name=self.session_name)
        starter = getattr(self.client, "start", None)
        if starter is not None:
            await _resolve(starter())

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

    async def send_document(self, path: Path, caption: str) -> Any:
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
        if result is None:
            raise RuntimeError("Rubpy returned no result for send_message")
        # A returned API response, including structured error responses, is retained
        # for callers/logging; transport exceptions propagate to the retry queue.
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
