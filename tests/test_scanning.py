import asyncio
import unittest
from types import SimpleNamespace

from telerubik.cli import _iter_source_messages


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def iter_messages(self, chat_ref, **kwargs):
        self.calls.append((chat_ref, kwargs))

        async def generate():
            for message in self.messages:
                yield message

        return generate()


class SourceScanningTests(unittest.TestCase):
    def test_incremental_scan_streams_new_messages_in_ascending_order(self):
        async def scenario():
            messages = [SimpleNamespace(id=value) for value in (11, 12, 13)]
            client = FakeClient(messages)
            result = [
                message async for message in _iter_source_messages(
                    client, "@source", cursor=10, lookback_limit=25
                )
            ]
            self.assertEqual([message.id for message in result], [11, 12, 13])
            self.assertEqual(
                client.calls,
                [("@source", {"min_id": 10, "reverse": True})],
            )

        asyncio.run(scenario())

    def test_initial_scan_is_bounded_and_oldest_first(self):
        async def scenario():
            messages = [SimpleNamespace(id=value) for value in (13, 12, 11)]
            client = FakeClient(messages)
            result = [
                message async for message in _iter_source_messages(
                    client, "@source", cursor=0, lookback_limit=3
                )
            ]
            self.assertEqual([message.id for message in result], [11, 12, 13])
            self.assertEqual(client.calls, [("@source", {"limit": 3})])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
