from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from telerubik.adapters import RubikaSender


class RubikaSenderTests(unittest.TestCase):
    def test_upload_then_send_uses_uploaded_file_metadata_and_caption(self):
        async def scenario():
            fake_client = Mock()
            fake_client.start = AsyncMock()
            fake_client.upload = AsyncMock(return_value={"file_id": "abc", "access_hash": "xyz"})
            fake_client.send_message = AsyncMock(return_value={"status": "OK"})
            fake_client.disconnect = AsyncMock()
            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = Mock(return_value=fake_client)
            with patch.dict(sys.modules, {"rubpy": rubpy_module}):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "episode.mkv"
                    path.write_bytes(b"video")
                    sender = RubikaSender("./private/rubika_session", "destination-guid")
                    await sender.start()
                    result = await sender.send_document(path, "Episode caption")
                    self.assertEqual(result, {"status": "OK"})
                    rubpy_module.Client.assert_called_once_with(name="./private/rubika_session")
                    fake_client.upload.assert_awaited_once_with(str(path), file_name="episode.mkv")
                    fake_client.send_message.assert_awaited_once_with(
                        object_guid="destination-guid",
                        text="Episode caption",
                        file_inline={"file_id": "abc", "access_hash": "xyz"},
                    )
                    await sender.close()
                    fake_client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_empty_caption_is_sent_as_none(self):
        async def scenario():
            fake_client = Mock()
            fake_client.upload = AsyncMock(return_value={"file_id": "abc"})
            fake_client.send_message = AsyncMock(return_value={"status": "OK"})
            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = Mock(return_value=fake_client)
            with patch.dict(sys.modules, {"rubpy": rubpy_module}):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "file.bin"
                    path.write_bytes(b"x")
                    sender = RubikaSender("session", "destination")
                    await sender.start()
                    await sender.send_document(path, "  ")
                    self.assertIsNone(fake_client.send_message.await_args.kwargs["text"])
                    await sender.close()

        asyncio.run(scenario())

    def test_missing_upload_metadata_fails_closed(self):
        async def scenario():
            fake_client = Mock()
            fake_client.upload = AsyncMock(return_value=None)
            fake_client.send_message = AsyncMock()
            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = Mock(return_value=fake_client)
            with patch.dict(sys.modules, {"rubpy": rubpy_module}):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "file.bin"
                    path.write_bytes(b"x")
                    sender = RubikaSender("session", "destination")
                    await sender.start()
                    with self.assertRaisesRegex(RuntimeError, "no usable file metadata"):
                        await sender.send_document(path, "caption")
                    fake_client.send_message.assert_not_awaited()
                    await sender.close()

        asyncio.run(scenario())

    def test_missing_local_file_is_not_uploaded(self):
        async def scenario():
            fake_client = Mock()
            fake_client.upload = AsyncMock()
            fake_client.send_message = AsyncMock()
            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = Mock(return_value=fake_client)
            with patch.dict(sys.modules, {"rubpy": rubpy_module}):
                sender = RubikaSender("session", "destination")
                await sender.start()
                with self.assertRaisesRegex(RuntimeError, "does not exist"):
                    await sender.send_document(Path("/no/such/file"), "")
                fake_client.upload.assert_not_awaited()
                await sender.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
