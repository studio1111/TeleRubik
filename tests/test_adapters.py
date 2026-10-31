from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telerubik.adapters import RubikaSender


class NotRegistered(Exception):
    pass


class AdapterUploadTests(unittest.TestCase):
    def test_not_registered_upload_triggers_one_device_registration_then_retries(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "clip.mkv"
                source.write_bytes(b"video-data")
                client = type("FakeClient", (), {})()
                client.upload = AsyncMock(
                    side_effect=[NotRegistered("NOT_REGISTERED"), {"file_id": "1", "dc_id": 1}]
                )
                client.register_device = AsyncMock(return_value=object())
                client.send_message = AsyncMock(
                    return_value={"status": "OK", "status_det": "OK", "message_id": "m1"}
                )
                sender = RubikaSender("session", "destination")
                sender.client = client

                await sender._send_single_document(source, "caption")

                client.register_device.assert_awaited_once_with()
                self.assertEqual(client.upload.await_count, 2)
                client.send_message.assert_awaited_once()
                inline = client.send_message.await_args.kwargs["file_inline"]
                self.assertEqual(inline["type"], "Video")
                self.assertFalse(inline["is_spoil"])

        asyncio.run(scenario())

    def test_large_file_uses_sequential_500mb_style_zip_parts_and_manifest(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "large.mkv"
                source.write_bytes(b"0123456789abcdef")
                client = type("FakeClient", (), {})()
                client.upload = AsyncMock(side_effect=lambda path, **kwargs: {"file_id": Path(path).name})
                client.send_message = AsyncMock(
                    return_value={"status": "OK", "status_det": "OK", "message_id": "m1"}
                )
                sender = RubikaSender("session", "destination")
                sender.client = client

                with patch("telerubik.adapters.LARGE_FILE_PART_SIZE", 6), patch(
                    "telerubik.adapters.LARGE_FILE_SPLIT_THRESHOLD", 5
                ):
                    result = await sender.send_document(source, "original caption")

                self.assertTrue(result["chunked"])
                self.assertEqual(result["parts"], 3)
                self.assertEqual(client.upload.await_count, 4)  # 3 ZIPs + manifest
                sent_names = [
                    call.kwargs["file_inline"]["file_id"]
                    for call in client.send_message.await_args_list
                ]
                self.assertEqual(len(sent_names), 4)
                captions = [call.kwargs.get("text") or "" for call in client.send_message.await_args_list]
                self.assertIn("original caption", captions[0])
                self.assertIn("PART 003/003", captions[2])
                self.assertIn("MANIFEST", captions[3])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
