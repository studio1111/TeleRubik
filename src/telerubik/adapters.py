from __future__ import annotations
import inspect
from pathlib import Path
from typing import Any

class RubikaSender:
    """Rubpy adapter; API mismatches fail loudly rather than silently dropping files."""
    def __init__(self, bot_token: str, destination_guid: str):
        self.bot_token, self.destination_guid = bot_token, destination_guid
        self.client: Any = None

    async def start(self) -> None:
        try: from rubpy.bot import BotClient
        except ImportError as exc: raise RuntimeError("Rubpy is missing; install requirements.txt") from exc
        self.client = BotClient(self.bot_token)
        starter = getattr(self.client, "start", None)
        if starter is not None:
            result = starter()
            if inspect.isawaitable(result): await result

    async def send_document(self, path: Path, caption: str) -> None:
        if self.client is None: raise RuntimeError("Rubika sender has not been started")
        method = getattr(self.client, "send_document", None)
        if method is None: raise RuntimeError("Rubpy BotClient has no send_document method; verify installed API before sending")
        result = method(self.destination_guid, document=str(path), caption=caption)
        if inspect.isawaitable(result): await result

    async def close(self) -> None:
        if self.client is None: return
        for name in ("stop", "close", "disconnect"):
            method = getattr(self.client, name, None)
            if method is not None:
                result = method()
                if inspect.isawaitable(result): await result
                break
        self.client = None
