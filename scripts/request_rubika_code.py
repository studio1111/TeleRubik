"""Create a local Rubpy 7.3.5 user session without echoing login secrets.

The phone number must be international. Two-step password and login code are
requested only through getpass and are never printed or persisted by this script.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import stat
from getpass import getpass
from pathlib import Path
from typing import Callable


def normalize_international_phone(value: str) -> str:
    """Return the digits-only international number expected by Rubpy's sendCode API."""
    if not isinstance(value, str):
        raise ValueError("Phone number must be text")
    value = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    value = value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if not value.startswith("+"):
        raise ValueError("Use international format with + and country code")
    digits = value[1:]
    if not re.fullmatch(r"[1-9]\d{6,14}", digits):
        raise ValueError("Phone number must contain 7 to 15 digits and a valid country-code prefix")
    return digits


def safe_error_fields(exc: BaseException) -> tuple[str, str]:
    """Extract only known API error identifiers; never expose raw exception text."""
    status = getattr(exc, "status", None)
    detail = getattr(exc, "status_det", None)
    for candidate in getattr(exc, "args", ()):
        if isinstance(candidate, dict):
            status = status or candidate.get("status")
            detail = detail or candidate.get("status_det")
        elif isinstance(candidate, str):
            status_match = re.search(r"""['"]status['"]\s*:\s*['"]([A-Z0-9_]{1,64})['"]""", candidate)
            detail_match = re.search(r"""['"]status_det['"]\s*:\s*['"]([A-Z0-9_]{1,64})['"]""", candidate)
            status = status or (status_match.group(1) if status_match else None)
            detail = detail or (detail_match.group(1) if detail_match else None)

    def valid_identifier(value: object) -> str:
        text_value = str(value or "")
        return text_value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", text_value) else "unknown"

    return valid_identifier(status), valid_identifier(detail)


def _load_crypto_primitives():
    from rubpy.crypto import Crypto
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15

    return Crypto, RSA, pkcs1_15


def _validate_session_target(session_name: str) -> tuple[Path, Path]:
    """Prepare a private session directory and refuse symlinks/authenticated overwrite."""
    name = Path(session_name).expanduser()
    session_file = name if name.suffix == ".rp" else Path(f"{name}.rp")
    parent_was_present = session_file.parent.exists()
    session_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_was_present:
        os.chmod(session_file.parent, 0o700)
    if session_file.is_symlink():
        raise ValueError("Refusing to use a symlink session path")
    if session_file.exists():
        try:
            connection = sqlite3.connect(f"file:{session_file}?mode=ro", uri=True)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not {"version", "session"}.issubset(tables):
                    raise ValueError("Existing session path is not a Rubpy database")
                row = connection.execute("SELECT auth FROM session LIMIT 1").fetchone()
                if row and row[0]:
                    raise ValueError("An authenticated session already exists at this path; refusing to overwrite it")
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ValueError("Existing session database could not be safely validated") from exc
    return name, session_file


async def authenticate_client(
    client,
    phone_number: str,
    password_prompt: Callable[[str], str] | None = None,
    code_prompt: Callable[[str], str] | None = None,
) -> None:
    """Run Rubpy's sendCode -> optional pass_key -> signIn -> registerDevice flow."""
    password_prompt = password_prompt or getpass
    code_prompt = code_prompt or getpass

    result = await client.send_code(phone_number=phone_number, send_type="SMS")
    if getattr(result, "status", None) == "SendPassKey":
        pass_key = password_prompt("Rubika two-step verification password (hidden): ")
        if not pass_key:
            raise ValueError("Two-step verification password cannot be empty")
        result = await client.send_code(
            phone_number=phone_number,
            pass_key=pass_key,
            send_type="SMS",
        )

    if getattr(result, "status", None) != "OK":
        raise RuntimeError("Rubika did not accept the login-code request")

    phone_code_hash = getattr(result, "phone_code_hash", None)
    if not isinstance(phone_code_hash, str) or not phone_code_hash:
        raise RuntimeError("Rubika response did not contain a login-code reference")

    phone_code = code_prompt("Rubika login code (hidden): ").strip()
    if not phone_code:
        raise ValueError("Login code cannot be empty")

    Crypto, RSA, pkcs1_15 = _load_crypto_primitives()
    public_key, private_key = Crypto.create_keys()
    signed_in = await client.sign_in(
        phone_code=phone_code,
        phone_number=phone_number,
        phone_code_hash=phone_code_hash,
        public_key=public_key,
    )
    if getattr(signed_in, "status", None) != "OK":
        raise RuntimeError("Rubika did not complete sign-in")

    encrypted_auth = getattr(signed_in, "auth", None)
    user = getattr(signed_in, "user", None)
    user_guid = getattr(user, "user_guid", None)
    account_phone = getattr(user, "phone", None) or phone_number
    if not encrypted_auth or not user_guid:
        raise RuntimeError("Rubika sign-in response is missing required account data")

    auth = Crypto.decrypt_RSA_OAEP(private_key, encrypted_auth)
    client.private_key = private_key
    client.auth = auth
    client.key = Crypto.passphrase(auth)
    client.decode_auth = Crypto.decode_auth(auth)
    client.import_key = pkcs1_15.new(RSA.import_key(private_key.encode()))
    client.guid = user_guid
    client.session.insert(
        phone_number=account_phone,
        auth=auth,
        guid=user_guid,
        user_agent=client.user_agent,
        private_key=private_key,
    )
    await client.register_device(device_model=client.name)

    verified = await client.get_me()
    verified_guid = getattr(getattr(verified, "user", None), "user_guid", None)
    if verified_guid != user_guid:
        raise RuntimeError("Authenticated session verification did not match the signed-in account")


async def main() -> int:
    from rubpy import Client

    try:
        phone = normalize_international_phone(
            getpass("International phone number (hidden): ").strip()
        )
    except ValueError as exc:
        print("Input error:", str(exc))
        return 2

    session_name = os.environ.get("RUBIKA_SESSION_NAME", "private/rubika_session").strip()
    if not session_name:
        print("Input error: RUBIKA_SESSION_NAME cannot be empty")
        return 2
    try:
        name, session_file = _validate_session_target(session_name)
    except ValueError as exc:
        print("Session path error:", str(exc))
        return 2

    logger = logging.getLogger("telerubik.rubika_auth")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.WARNING)
    logger.propagate = False

    client = Client(
        name=str(name),
        platform="Android",
        max_retries=1,
        timeout=20,
        logger=logger,
    )
    # SQLite creates the file with the process umask; immediately restrict it.
    if session_file.exists():
        os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)

    try:
        await client.connect()
        print("Connection: OK")
        await authenticate_client(client, phone)
        os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)
        print("Authentication: OK")
        print("Session: saved and verified locally")
        print("Keep the .rp file private. It grants access to the Rubika account.")
        return 0
    except Exception as exc:
        status, detail = safe_error_fields(exc)
        print("Authentication failed:", type(exc).__name__)
        print("API status:", status)
        print("API detail:", detail)
        return 1
    finally:
        if getattr(client, "connection", None):
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
