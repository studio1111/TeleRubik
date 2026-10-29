from __future__ import annotations
import inspect
from pathlib import Path
from typing import Any
from .queue import Job

class RubikaSender:
    """Rubpy BotClient adapter for text, image, video, and generic file messages."""
    def __init__(self, bot_token: str, destination_guid: str):
        self.bot_token, self.destination_guid = bot_token, destination_guid
        self.client: Any = None

    async def start(self) -> None:
        try:
            from rubpy.bot import BotClient
        except ImportError as exc:
            raise RuntimeError("Rubpy is missing; install requirements.txt") from exc
        self.client = BotClient(token=self.bot_token)
        starter = getattr(self.client, "start", None)
        if starter is not None:
            result = starter()
            if inspect.isawaitable(result):
                await result

    async def _resolve(self, result: Any) -> None:
        if inspect.isawaitable(result):
            await result

    async def send_message(self, text: str) -> None:
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        method = getattr(self.client, "send_message", None)
        if method is None:
            raise RuntimeError("Rubpy BotClient has no send_message method")
        await self._resolve(method(self.destination_guid, text))

    async def send_media(self, path: Path, caption: str, media_type: str = "file") -> None:
        if self.client is None:
            raise RuntimeError("Rubika sender has not been started")
        method = getattr(self.client, "send_file", None)
        if method is None:
            raise RuntimeError("Rubpy BotClient has no send_file method; verify installed API before sending")
        type_map = {"photo": "Image", "video": "Video", "file": "File"}
        rubpy_type = type_map.get(media_type, "File")
        result = method(self.destination_guid, file=str(path), text=caption, file_name=path.name, type=rubpy_type)
        await self._resolve(result)

    async def send_job(self, path: Path | None, caption: str, job: Job) -> None:
        if job.payload.get("kind") == "text":
            await self.send_message(caption)
            return
        if path is None:
            raise RuntimeError("Media job has no downloaded file")
        await self.send_media(path, caption, job.payload.get("media_type", "file"))

    async def close(self) -> None:
        if self.client is None:
            return
        for name in ("stop", "close", "disconnect"):
            method = getattr(self.client, name, None)
            if method is not None:
                result = method()
                if inspect.isawaitable(result):
                    await result
                break
        self.client = None
