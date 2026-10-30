from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock, patch

from rubpy.types import Update as RubpyUpdate

from telerubik.adapters import RubikaSender, _validate_authenticated_session


def fake_rubpy_modules(client):
    rubpy_module = types.ModuleType("rubpy")
    rubpy_module.__path__ = []
    rubpy_module.Client = Mock(return_value=client)

    crypto_module = types.ModuleType("rubpy.crypto")
    crypto_module.Crypto = types.SimpleNamespace(
        decode_auth=Mock(return_value="decoded-auth"),
        passphrase=Mock(return_value="derived-session-key"),
    )

    crypto_package = types.ModuleType("Crypto")
    crypto_package.__path__ = []
    public_key_module = types.ModuleType("Crypto.PublicKey")
    public_key_module.RSA = types.SimpleNamespace(import_key=Mock(return_value="rsa-key"))
    signature_module = types.ModuleType("Crypto.Signature")
    signature_module.pkcs1_15 = types.SimpleNamespace(new=Mock(return_value="signing-key"))

    aiohttp_module = types.ModuleType("aiohttp")
    verified_session = types.SimpleNamespace(close=AsyncMock())
    aiohttp_module.TCPConnector = Mock(return_value="verified-connector")
    aiohttp_module.ClientTimeout = Mock(return_value="verified-timeout")
    aiohttp_module.ClientSession = Mock(return_value=verified_session)

    return {
        "aiohttp": aiohttp_module,
        "rubpy": rubpy_module,
        "rubpy.crypto": crypto_module,
        "Crypto": crypto_package,
        "Crypto.PublicKey": public_key_module,
        "Crypto.Signature": signature_module,
    }


def make_fake_client():
    client = Mock()
    client.auth = "fake-auth"
    client.private_key = "fake-private-key"
    client.connect = AsyncMock()
    client.original_session = types.SimpleNamespace(close=AsyncMock())
    client.connection = types.SimpleNamespace(
        session=client.original_session,
        headers={"user-agent": "test-agent"},
    )
    client.timeout = 20
    client.get_me = AsyncMock(
        return_value=types.SimpleNamespace(
            user=types.SimpleNamespace(user_guid="authenticated-user-guid")
        )
    )
    client.disconnect = AsyncMock()
    return client


class RubikaSessionValidationTests(unittest.TestCase):
    def test_accepts_authenticated_rubpy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rubika.rp"
            db = __import__("sqlite3").connect(path)
            db.executescript(
                "CREATE TABLE version(version integer primary key);"
                "INSERT INTO version VALUES (1);"
                "CREATE TABLE session(phone text, auth text, guid text, agent text, private_key text);"
                "INSERT INTO session VALUES ('phone', 'auth', 'guid', 'agent', 'private-key');"
            )
            db.commit()
            db.close()
            self.assertEqual(_validate_authenticated_session(str(path)), path)

    def test_rejects_missing_session_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "authenticated Rubpy"):
                _validate_authenticated_session(str(Path(directory) / "missing.rp"))

    def test_rejects_empty_or_unauthed_session_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.rp"
            db = __import__("sqlite3").connect(path)
            db.executescript(
                "CREATE TABLE version(version integer primary key);"
                "INSERT INTO version VALUES (1);"
                "CREATE TABLE session(phone text, auth text, guid text, agent text, private_key text);"
            )
            db.commit()
            db.close()
            with self.assertRaisesRegex(RuntimeError, "not authenticated"):
                _validate_authenticated_session(str(path))


class RubikaSenderTests(unittest.TestCase):
    def test_accepts_pinned_rubpy_update_payload_after_builder_validates_outer_status(self):
        result = RubpyUpdate({"message_id": "rubika-message-123", "object_guid": "destination"})
        RubikaSender._validate_send_result(result)

    def test_rejects_rubpy_update_without_message_identifier(self):
        result = RubpyUpdate({"file_inline": {"file_id": "uploaded-only"}})
        with self.assertRaisesRegex(RuntimeError, "verifiable success result"):
            RubikaSender._validate_send_result(result)

    def _start_sender(self, sender, fake_client, modules):
        stack = patch.dict(sys.modules, modules)
        stack.start()
        validation = patch(
            "telerubik.adapters._validate_authenticated_session",
            return_value=Path("private/rubika_session.rp"),
        )
        validation.start()
        self.addCleanup(validation.stop)
        self.addCleanup(stack.stop)
        return fake_client

    def test_upload_then_send_uses_uploaded_file_metadata_and_caption(self):
        async def scenario():
            fake_client = make_fake_client()

            async def verify_key_before_api_call():
                self.assertEqual(fake_client.key, "derived-session-key")
                return types.SimpleNamespace(
                    user=types.SimpleNamespace(user_guid="authenticated-user-guid")
                )

            fake_client.get_me.side_effect = verify_key_before_api_call
            fake_client.upload = AsyncMock(return_value={"file_id": "abc", "access_hash": "xyz"})
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "episode.mkv"
                        path.write_bytes(b"video")
                        sender = RubikaSender("./private/rubika_session", "destination-guid")
                        await sender.start()
                        result = await sender.send_document(path, "Episode caption")
                        self.assertEqual(result, {"status": "OK", "status_det": "OK"})
                        rubpy_modules["rubpy"].Client.assert_called_once_with(
                            name="./private/rubika_session", platform="Android", logger=ANY
                        )
                        fake_client.connect.assert_awaited_once()
                        fake_client.original_session.close.assert_awaited_once()
                        aiohttp_module = rubpy_modules["aiohttp"]
                        aiohttp_module.TCPConnector.assert_called_once_with(ssl=True, limit=100)
                        aiohttp_module.ClientTimeout.assert_called_once_with(total=20)
                        aiohttp_module.ClientSession.assert_called_once_with(
                            connector="verified-connector",
                            headers={"user-agent": "test-agent"},
                            timeout="verified-timeout",
                        )
                        fake_client.start.assert_not_called()
                        fake_client.get_me.assert_awaited_once()
                        self.assertEqual(fake_client.guid, "authenticated-user-guid")
                        rubpy_modules["rubpy.crypto"].Crypto.passphrase.assert_called_once_with("fake-auth")
                        fake_client.upload.assert_awaited_once_with(str(path), file_name="episode.mkv")
                        fake_client.send_message.assert_awaited_once_with(
                            object_guid="destination-guid",
                            text="Episode caption",
                            file_inline={"file_id": "abc", "access_hash": "xyz"},
                        )
                        await sender.close()
                        fake_client.disconnect.assert_awaited_once()

        asyncio.run(scenario())

    def test_upload_result_with_to_dict_property_is_forwarded_without_private_attributes(self):
        class UploadUpdate:
            def __init__(self):
                self.client = object()
                self.original_update = {"file_id": "property-id", "access_hash": "property-hash"}

            @property
            def to_dict(self):
                return self.original_update

        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(return_value=UploadUpdate())
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        await sender.send_document(path, "caption")
                        self.assertEqual(
                            fake_client.send_message.await_args.kwargs["file_inline"],
                            {"file_id": "property-id", "access_hash": "property-hash"},
                        )
                        await sender.close()

        asyncio.run(scenario())

    def test_empty_caption_is_sent_as_none(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(return_value={"file_id": "abc"})
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        await sender.send_document(path, "  ")
                        self.assertIsNone(fake_client.send_message.await_args.kwargs["text"])
                        await sender.close()

        asyncio.run(scenario())

    def test_text_only_post_skips_upload_and_preserves_text(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    sender = RubikaSender("session", "destination")
                    await sender.start()
                    await sender.send_document(None, "Text post")
                    fake_client.upload.assert_not_called()
                    fake_client.send_message.assert_awaited_once_with(
                        object_guid="destination", text="Text post"
                    )
                    await sender.close()

        asyncio.run(scenario())

    def test_rejects_incomplete_status_pair(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete success status pair"):
            RubikaSender._validate_send_result({"status": "OK"})

    def test_rejects_non_success_status_detail_even_when_status_is_ok(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(return_value={"file_id": "abc"})
            fake_client.send_message = AsyncMock(
                return_value={"status": "OK", "status_det": "ERROR"}
            )
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        with self.assertRaisesRegex(RuntimeError, "response detail"):
                            await sender.send_document(path, "caption")
                        await sender.close()

        asyncio.run(scenario())

    def test_rejects_response_without_explicit_success_status(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(return_value={"file_id": "abc"})
            fake_client.send_message = AsyncMock(return_value={"message_id": "unverified"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        with self.assertRaisesRegex(RuntimeError, "verifiable success result"):
                            await sender.send_document(path, "caption")
                        await sender.close()

        asyncio.run(scenario())

    def test_missing_upload_metadata_fails_closed(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(return_value=None)
            fake_client.send_message = AsyncMock()
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
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
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock()
            fake_client.send_message = AsyncMock()
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    sender = RubikaSender("session", "destination")
                    await sender.start()
                    with self.assertRaisesRegex(RuntimeError, "does not exist"):
                        await sender.send_document(Path("/no/such/file"), "")
                    fake_client.upload.assert_not_awaited()
                    await sender.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
