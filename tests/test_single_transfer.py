from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telerubik.adapters import _register_rubpy_device
from telerubik.config import Settings
from telerubik.single_transfer import transfer_one


def settings_for(directory: str) -> Settings:
    return Settings(
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_path=Path(directory) / "telegram",
        telegram_session_string="telegram-session",
        telegram_source_chats=("@Animehighbo",),
        rubika_session_name=str(Path(directory) / "rubika.rp"),
        rubika_destination_guid="private-destination",
        state_db=Path(directory) / "state.sqlite3",
        download_dir=Path(directory) / "downloads",
        max_file_size_bytes=1000,
        max_retries=1,
        retry_base_seconds=1,
        poll_seconds=1,
        lookback_limit=1,
        allowed_user_ids=frozenset(),
        dry_run=False,
    )


class SingleTransferTests(unittest.TestCase):
    def _telethon_modules(self, client):
        telethon = types.ModuleType("telethon")
        telethon.__path__ = []
        telethon.TelegramClient = lambda *args, **kwargs: client
        sessions = types.ModuleType("telethon.sessions")
        sessions.StringSession = lambda value: ("string-session", value)
        return {"telethon": telethon, "telethon.sessions": sessions}

    def test_transfers_only_the_requested_message_after_strict_validation(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                message = types.SimpleNamespace(
                    id=1734,
                    media=object(),
                    file=types.SimpleNamespace(
                        name="Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv",
                        size=7,
                    ),
                    message="approved caption",
                )
                client = types.SimpleNamespace(
                    connect=AsyncMock(),
                    is_user_authorized=AsyncMock(return_value=True),
                    get_entity=AsyncMock(return_value="source-entity"),
                    get_messages=AsyncMock(return_value=message),
                    download_media=AsyncMock(),
                    disconnect=AsyncMock(),
                )

                events = []

                async def download(_message, file):
                    events.append("download")
                    Path(file).write_bytes(b"1234567")
                    return file

                client.download_media.side_effect = download
                modules = self._telethon_modules(client)
                with patch.dict(sys.modules, modules):
                    with patch("telerubik.single_transfer.RubikaSender") as sender_type:
                        sender = sender_type.return_value
                        sender.start = AsyncMock(side_effect=lambda: events.append("rubika-auth"))
                        sender.send_document = AsyncMock()
                        sender.close = AsyncMock()
                        await transfer_one(
                            settings_for(directory),
                            "@Animehighbo",
                            1734,
                            "Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv",
                        )
                        client.get_messages.assert_awaited_once_with("source-entity", ids=1734)
                        sender.start.assert_awaited_once()
                        self.assertEqual(events, ["rubika-auth", "download"])
                        sender.send_document.assert_awaited_once()
                        sent_path, caption = sender.send_document.await_args.args
                        self.assertEqual(Path(sent_path).name, "Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv")
                        self.assertEqual(caption, "approved caption")
                        self.assertFalse(Path(sent_path).exists())
                        sender.close.assert_awaited_once()
                        client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_rejects_empty_media_before_rubika_auth_or_download(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                message = types.SimpleNamespace(
                    id=1734,
                    media=object(),
                    file=types.SimpleNamespace(name="empty.mkv", size=0),
                    message="caption",
                )
                client = types.SimpleNamespace(
                    connect=AsyncMock(),
                    is_user_authorized=AsyncMock(return_value=True),
                    get_entity=AsyncMock(return_value="source"),
                    get_messages=AsyncMock(return_value=message),
                    download_media=AsyncMock(),
                    disconnect=AsyncMock(),
                )
                with patch.dict(sys.modules, self._telethon_modules(client)):
                    with patch("telerubik.single_transfer.RubikaSender") as sender_type:
                        sender_type.return_value.start = AsyncMock()
                        sender_type.return_value.close = AsyncMock()
                        with self.assertRaisesRegex(RuntimeError, "size is missing, invalid"):
                            await transfer_one(settings_for(directory), "@Animehighbo", 1734, "empty.mkv")
                        sender_type.return_value.start.assert_not_awaited()
                        client.download_media.assert_not_awaited()
                        client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_sender_close_failure_still_disconnects_telegram(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                message = types.SimpleNamespace(
                    id=1734,
                    media=object(),
                    file=types.SimpleNamespace(name="approved.mkv", size=7),
                    message="caption",
                )
                client = types.SimpleNamespace(
                    connect=AsyncMock(),
                    is_user_authorized=AsyncMock(return_value=True),
                    get_entity=AsyncMock(return_value="source"),
                    get_messages=AsyncMock(return_value=message),
                    download_media=AsyncMock(),
                    disconnect=AsyncMock(),
                )

                async def download(_message, file):
                    Path(file).write_bytes(b"1234567")
                    return file

                client.download_media.side_effect = download
                with patch.dict(sys.modules, self._telethon_modules(client)):
                    with patch("telerubik.single_transfer.RubikaSender") as sender_type:
                        sender = sender_type.return_value
                        sender.start = AsyncMock()
                        sender.send_document = AsyncMock()
                        sender.close = AsyncMock(side_effect=RuntimeError("close failed"))
                        with self.assertRaisesRegex(RuntimeError, "close failed"):
                            await transfer_one(settings_for(directory), "@Animehighbo", 1734, "approved.mkv")
                        client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_rejects_wrong_filename_without_starting_rubika_sender(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                message = types.SimpleNamespace(
                    id=1734,
                    media=object(),
                    file=types.SimpleNamespace(name="different.mkv", size=10),
                    message="caption",
                )
                client = types.SimpleNamespace(
                    connect=AsyncMock(),
                    is_user_authorized=AsyncMock(return_value=True),
                    get_entity=AsyncMock(return_value="source"),
                    get_messages=AsyncMock(return_value=message),
                    download_media=AsyncMock(),
                    disconnect=AsyncMock(),
                )
                with patch.dict(sys.modules, self._telethon_modules(client)):
                    with patch("telerubik.single_transfer.RubikaSender") as sender_type:
                        sender_type.return_value.close = AsyncMock()
                        sender_type.return_value.start = AsyncMock()
                        sender_type.return_value.send_document = AsyncMock()
                        with self.assertRaisesRegex(RuntimeError, "does not match"):
                            await transfer_one(settings_for(directory), "@Animehighbo", 1734, "expected.mkv")
                        sender_type.return_value.start.assert_not_awaited()
                        client.download_media.assert_not_awaited()
                        client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_rejects_missing_telegram_string_session(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings = Settings(**{**settings.__dict__, "telegram_session_string": ""})
                with self.assertRaisesRegex(RuntimeError, "TELEGRAM_SESSION_STRING"):
                    await transfer_one(settings, "@Animehighbo", 1734, "expected.mkv")

        asyncio.run(scenario())

    def test_rejects_dry_run_mode(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                settings = settings_for(directory)
                settings = Settings(**{**settings.__dict__, "dry_run": True})
                with self.assertRaisesRegex(RuntimeError, "requires TELERUBIK_DRY_RUN=false"):
                    await transfer_one(settings, "@Animehighbo", 1734, "expected.mkv")

        asyncio.run(scenario())


class RubpyDeviceRegistrationTests(unittest.TestCase):
    def test_registers_ephemeral_worker_device(self):
        async def scenario():
            client = types.SimpleNamespace(register_device=AsyncMock(return_value=object()))
            await _register_rubpy_device(client)
            client.register_device.assert_awaited_once_with()

        asyncio.run(scenario())

    def test_fails_closed_when_rubpy_cannot_register_device(self):
        async def scenario():
            client = types.SimpleNamespace()
            with self.assertRaisesRegex(RuntimeError, "does not support register_device"):
                await _register_rubpy_device(client)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
