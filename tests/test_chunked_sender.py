from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from telerubik.adapters import RubikaSender


class ChunkedSenderTests(unittest.TestCase):
    def test_large_file_streams_one_part_at_a_time_then_manifest(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "video.mkv"
                payload = b"abcdefghij"
                path.write_bytes(payload)
                client = Mock()
                uploaded_payloads = []
                maximum_live_parts = 0

                async def upload(filename, file_name):
                    nonlocal maximum_live_parts
                    part_files = list(Path(filename).parent.glob("*.part*"))
                    maximum_live_parts = max(maximum_live_parts, len(part_files))
                    uploaded_payloads.append((file_name, Path(filename).read_bytes()))
                    return {"file_id": file_name}

                client.upload = AsyncMock(side_effect=upload)
                client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
                sender = RubikaSender("unused-session", "private-destination")
                sender.client = client

                with patch("telerubik.adapters.DEFAULT_CHUNK_SIZE", 4):
                    result = await sender.send_document(path, "original caption")

                self.assertEqual(result["status"], "OK")
                self.assertTrue(result["chunked"])
                self.assertEqual(result["parts"], 3)
                calls = client.send_message.await_args_list
                self.assertEqual(len(calls), 4)
                captions = [call.kwargs["text"] for call in calls]
                self.assertIn("PART 0001/0003", captions[0])
                self.assertIn("PART 0002/0003", captions[1])
                self.assertIn("PART 0003/0003", captions[2])
                self.assertIn("CHUNK MANIFEST", captions[3])
                self.assertEqual([item[1] for item in uploaded_payloads[:3]], [b"abcd", b"efgh", b"ij"])
                self.assertTrue(uploaded_payloads[-1][0].endswith(".manifest.json"))
                self.assertLessEqual(maximum_live_parts, 1)
                self.assertEqual(list(Path(temporary).glob("video.mkv.part*")), [])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
