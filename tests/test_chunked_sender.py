from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from telerubik.adapters import RubikaSender


class ChunkedSenderTests(unittest.TestCase):
    def test_large_file_sends_parts_in_order_then_manifest(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "video.mkv"
                path.write_bytes(b"abcdefghij")
                client = Mock()
                client.upload = AsyncMock(side_effect=lambda filename, file_name: {"file_id": Path(filename).name})
                client.send_message = AsyncMock(return_value={"status": "OK"})
                sender = RubikaSender("unused-session", "private-destination")
                sender.client = client

                with patch("telerubik.adapters.DEFAULT_CHUNK_SIZE", 4):
                    result = await sender.send_document(path, "original caption")

                self.assertEqual(result["status"], "OK")
                self.assertTrue(result["chunked"])
                self.assertEqual(result["parts"], 3)
                calls = client.send_message.await_args_list
                self.assertEqual(len(calls), 4)  # three parts plus manifest
                captions = [call.kwargs["text"] for call in calls]
                self.assertIn("PART 0001/0003", captions[0])
                self.assertIn("PART 0002/0003", captions[1])
                self.assertIn("PART 0003/0003", captions[2])
                self.assertIn("CHUNK MANIFEST", captions[3])
                uploaded_names = [
                    call.args[0] if call.args else call.kwargs["file_inline"]["file_id"]
                    for call in client.upload.await_args_list
                ]
                self.assertEqual(len(uploaded_names), 4)
                self.assertTrue(client.upload.await_args_list[-1].kwargs["file_name"].endswith(".manifest.json"))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
