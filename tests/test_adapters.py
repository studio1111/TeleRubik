from __future__ import annotations

import asyncio
import tempfile
import unittest
import sys
import types
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telerubik.adapters import RubikaSender


class NotRegistered(Exception):
    pass


class AdapterUploadTests(unittest.TestCase):
    def test_start_restores_rubpy_sqlite_fields_without_interactive_login(self):
        async def scenario():
            client_instances = []

            class FakeClient:
                def __init__(self, **kwargs):
                    self.session = SimpleNamespace(
                        information=lambda: (
                            "+989000000000", "auth-value", "saved-guid",
                            "stored-agent", "private-key"
                        )
                    )
                    self.auth = None
                    self.private_key = None
                    self.guid = None
                    self.start = AsyncMock(side_effect=AssertionError("interactive start must not run"))
                    self.get_me = AsyncMock(
                        return_value=SimpleNamespace(
                            user=SimpleNamespace(user_guid="saved-guid")
                        )
                    )
                    self.connect = AsyncMock()
                    self.disconnect = AsyncMock()
                    client_instances.append(self)

            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = FakeClient
            rubpy_crypto_module = types.ModuleType("rubpy.crypto")
            rubpy_crypto_module.Crypto = SimpleNamespace(
                passphrase=lambda value: f"key:{value}",
                decode_auth=lambda value: f"decoded:{value}",
            )
            crypto_module = types.ModuleType("Crypto")
            public_key_module = types.ModuleType("Crypto.PublicKey")
            public_key_module.RSA = SimpleNamespace(import_key=lambda value: f"rsa:{value}")
            signature_module = types.ModuleType("Crypto.Signature")
            signature_module.pkcs1_15 = SimpleNamespace(new=lambda value: f"signer:{value}")

            modules = {
                "rubpy": rubpy_module,
                "rubpy.crypto": rubpy_crypto_module,
                "Crypto": crypto_module,
                "Crypto.PublicKey": public_key_module,
                "Crypto.Signature": signature_module,
            }
            sender = RubikaSender("session", "destination")
            with patch.dict(sys.modules, modules), patch(
                "telerubik.adapters._validate_authenticated_session"
            ), patch(
                "telerubik.adapters._enable_verified_rubpy_transport",
                new=AsyncMock(),
            ):
                await sender.start()

            client = client_instances[0]
            self.assertEqual(client.auth, "auth-value")
            self.assertEqual(client.private_key, "private-key")
            self.assertEqual(client.guid, "saved-guid")
            self.assertEqual(client.phone_number, "+989000000000")
            self.assertEqual(client.user_agent, "stored-agent")
            self.assertEqual(client.key, "key:auth-value")
            self.assertEqual(client.decode_auth, "decoded:auth-value")
            self.assertEqual(client.import_key, "signer:rsa:b'private-key'")
            client.connect.assert_awaited_once_with()
            client.get_me.assert_awaited_once_with()
            client.start.assert_not_awaited()
            await sender.close()

        asyncio.run(scenario())

    def test_start_registers_device_once_when_saved_auth_is_not_registered(self):
        async def scenario():
            client_instances = []

            class FakeClient:
                def __init__(self, **kwargs):
                    self.session = SimpleNamespace(
                        information=lambda: (
                            "+989000000000", "auth-value", "saved-guid",
                            "stored-agent", "private-key"
                        )
                    )
                    self.get_me = AsyncMock(side_effect=[
                        NotRegistered("NOT_REGISTERED"),
                        SimpleNamespace(user=SimpleNamespace(user_guid="saved-guid")),
                    ])
                    self.register_device = AsyncMock(return_value=SimpleNamespace(status="OK"))
                    self.connect = AsyncMock()
                    self.disconnect = AsyncMock()
                    client_instances.append(self)

            rubpy_module = types.ModuleType("rubpy")
            rubpy_module.Client = FakeClient
            rubpy_crypto_module = types.ModuleType("rubpy.crypto")
            rubpy_crypto_module.Crypto = SimpleNamespace(
                passphrase=lambda value: f"key:{value}",
                decode_auth=lambda value: f"decoded:{value}",
            )
            crypto_module = types.ModuleType("Crypto")
            public_key_module = types.ModuleType("Crypto.PublicKey")
            public_key_module.RSA = SimpleNamespace(import_key=lambda value: f"rsa:{value}")
            signature_module = types.ModuleType("Crypto.Signature")
            signature_module.pkcs1_15 = SimpleNamespace(new=lambda value: f"signer:{value}")

            modules = {
                "rubpy": rubpy_module,
                "rubpy.crypto": rubpy_crypto_module,
                "Crypto": crypto_module,
                "Crypto.PublicKey": public_key_module,
                "Crypto.Signature": signature_module,
            }
            sender = RubikaSender("session", "destination")
            with patch.dict(sys.modules, modules), patch(
                "telerubik.adapters._validate_authenticated_session"
            ), patch(
                "telerubik.adapters._enable_verified_rubpy_transport",
                new=AsyncMock(),
            ):
                await sender.start()

            client = client_instances[0]
            client.register_device.assert_awaited_once_with()
            self.assertEqual(client.get_me.await_count, 2)
            self.assertEqual(client.guid, "saved-guid")
            await sender.close()

        asyncio.run(scenario())

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
