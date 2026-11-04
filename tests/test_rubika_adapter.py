from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock, patch

from PIL import Image
from rubpy.methods.utilities.thumbnail import MediaThumbnail
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
    client.auth = None
    client.private_key = None
    client.connect = AsyncMock()
    client.start = AsyncMock()
    client.guid = None
    client.phone_number = None
    client.user_agent = None
    client.session = types.SimpleNamespace(
        information=lambda: (
            "phone", "fake-auth", "authenticated-user-guid",
            "test-agent", "fake-private-key"
        )
    )
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



class RubpyImageThumbnailDependencyTests(unittest.TestCase):
    def test_pillow_generates_required_image_thumbnail_metadata(self):
        source = io.BytesIO()
        Image.new("RGB", (64, 32), color="white").save(source, format="JPEG")

        thumbnail = MediaThumbnail.from_image(source.getvalue())

        self.assertIsNotNone(thumbnail)
        self.assertEqual(thumbnail.width, 64)
        self.assertEqual(thumbnail.height, 32)
        self.assertTrue(thumbnail.image)


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

    def test_native_send_does_not_fall_back_to_legacy_upload(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock(side_effect=AssertionError("legacy upload must not run"))
            fake_client.send_message = AsyncMock(
                return_value={"status": "OK", "status_det": "OK"}
            )
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        await sender.send_document(path, "caption")
                        fake_client.upload.assert_not_awaited()
                        fake_client.send_message.assert_awaited_once()
                        await sender.close()

        asyncio.run(scenario())

    def test_native_send_message_receives_file_path_and_caption(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "episode.mkv"
                        path.write_bytes(b"video")
                        sender = RubikaSender("session", "destination-guid")
                        await sender.start()
                        await sender.send_document(path, "Episode caption")
                        fake_client.upload.assert_not_called()
                        kwargs = fake_client.send_message.await_args.kwargs
                        self.assertEqual(kwargs["object_guid"], "destination-guid")
                        self.assertEqual(kwargs["text"], "Episode caption")
                        self.assertEqual(kwargs["file_inline"], str(path))
                        self.assertEqual(kwargs["type"], "Video")
                        self.assertEqual(kwargs["file_name"], "episode.mkv")
                        await sender.close()

        asyncio.run(scenario())

    def test_image_uses_generic_file_mode_without_thumbnail(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.send_message = AsyncMock(return_value={"status": "OK", "status_det": "OK"})
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "sample.jpg"
                        path.write_bytes(b"image-bytes")
                        sender = RubikaSender("session", "destination-guid")
                        await sender.start()
                        await sender.send_document(path, "Image caption")
                        kwargs = fake_client.send_message.await_args.kwargs
                        self.assertEqual(kwargs["type"], "File")
                        self.assertFalse(kwargs["thumb"])
                        self.assertEqual(kwargs["file_name"], "sample.jpg")
                        await sender.close()

        asyncio.run(scenario())

    def test_native_send_does_not_expose_upload_metadata_to_adapter(self):
        async def scenario():
            fake_client = make_fake_client()
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
                        self.assertEqual(fake_client.send_message.await_args.kwargs["file_inline"], str(path))
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

    def test_missing_native_send_method_fails_closed(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.send_message = None
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "file.bin"
                        path.write_bytes(b"x")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        with self.assertRaisesRegex(RuntimeError, "lacks send_message"):
                            await sender.send_document(path, "caption")
                        await sender.close()

        asyncio.run(scenario())

    def test_empty_file_is_rejected_before_upload(self):
        async def scenario():
            fake_client = make_fake_client()
            fake_client.upload = AsyncMock()
            fake_client.send_message = AsyncMock()
            rubpy_modules = fake_rubpy_modules(fake_client)
            with patch.dict(sys.modules, rubpy_modules):
                with patch("telerubik.adapters._validate_authenticated_session"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "empty.bin"
                        path.write_bytes(b"")
                        sender = RubikaSender("session", "destination")
                        await sender.start()
                        with self.assertRaisesRegex(RuntimeError, "Empty files"):
                            await sender.send_document(path, "caption")
                        fake_client.upload.assert_not_awaited()
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
