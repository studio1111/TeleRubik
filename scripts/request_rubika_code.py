"""Safely request a Rubika login code using Rubpy 7.3.5 phone formatting.

This script only requests a code. It never asks for or prints an OTP or password.
Enter an international number (for example +<country-code><national-number>).
"""
from __future__ import annotations

import asyncio
import re
from getpass import getpass


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
            # Rubpy 7.3.5 may stringify its response dictionary in the exception.
            status_match = re.search(r"""['"]status['"]\s*:\s*['"]([A-Z0-9_]{1,64})['"]""", candidate)
            detail_match = re.search(r"""['"]status_det['"]\s*:\s*['"]([A-Z0-9_]{1,64})['"]""", candidate)
            status = status or (status_match.group(1) if status_match else None)
            detail = detail or (detail_match.group(1) if detail_match else None)

    def valid_identifier(value: object) -> str:
        text_value = str(value or "")
        return text_value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", text_value) else "unknown"

    return valid_identifier(status), valid_identifier(detail)


async def main() -> int:
    from rubpy import Client

    try:
        phone = normalize_international_phone(
            getpass("International phone number (hidden): ").strip()
        )
    except ValueError as exc:
        print("Input error:", str(exc))
        return 2

    client = Client(
        name="private/rubika_session",
        platform="Android",
        max_retries=1,
        timeout=20,
    )
    try:
        await client.connect()
        print("Connection: OK")
        result = await client.send_code(phone_number=phone, send_type="SMS")
        print("send_code status:", getattr(result, "status", "unknown"))
        if getattr(result, "status", None) == "SendPassKey":
            print("The account requires its Rubika two-step verification password.")
        elif getattr(result, "status", None) == "OK":
            print("A login code was requested. Enter it only in a trusted local login flow, never in chat.")
        return 0
    except Exception as exc:
        status, detail = safe_error_fields(exc)
        print("Request failed:", type(exc).__name__)
        print("API status:", status)
        print("API detail:", detail)
        return 1
    finally:
        if getattr(client, "connection", None):
            await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
