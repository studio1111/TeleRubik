# TeleRubik 🚚

**TeleRubik** transfers text-only posts and posts with media from Telegram chats you are authorized to read to a Rubika destination you control. It is independently implemented and borrows the upload pattern observed in Walrus: use a Rubpy **user-account Client**, upload the complete file with `client.upload`, then send the returned metadata with `client.send_message(file_inline=...)`. It does not use the Rubpy BotClient path for media uploads.

> **Status:** the pinned `rubpy==7.3.5` source contract has been inspected and the adapter now handles its session-key, upload-streaming and response-object behavior. The adapter also replaces Rubpy's TLS-verification-disabled HTTP session with certificate-verified TLS before authenticated requests. The latest CI matrix passed on Python 3.11, 3.12 and 3.13, and CodeQL plus dependency audit passed. A live authenticated runtime test and end-to-end transfer to a private destination are still pending.

## Current capabilities

- Telethon polling of configured Telegram chats, including text-only posts and media captions.
- Durable, idempotent SQLite queue keyed by source chat and message ID.
- Bounded retries and recovery of jobs interrupted while processing.
- Monotonic per-source Telegram cursors: a bounded initial import is followed by complete incremental scans after restarts or downtime.
- SHA-256-verified download cache: failed live uploads retain valid downloads for retry/restart, while successful sends and dry-runs clean them up.
- Filename normalization, file-size limits, download-path boundary checks and dry-run by default.
- Rubpy user-session upload flow: upload file, then send the uploaded file metadata and original caption.
- Unit tests and GitHub Actions security/CI workflows.
- Local-first state; no paid cloud service is required.

## Requirements

- Python 3.11+
- Telegram API ID/hash from https://my.telegram.org and permission to read configured chats.
- A pre-authenticated Rubpy **user-account session** and a destination GUID you control.
- `rubpy==7.3.5` (pinned in project dependencies).

## Configuration

Copy `.env.example` to `.env` and configure:

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
- `TELEGRAM_SOURCE_CHATS`, comma-separated
- Optional `TELEGRAM_SESSION_STRING` for a hosted worker; otherwise the local `TELEGRAM_SESSION_PATH` file is used. The worker connects only with an already-authorized session and never starts an interactive login.
- `RUBIKA_SESSION_NAME`, path/name for the Rubpy user session
- `RUBIKA_DESTINATION_GUID`
- Optional: `TELEGRAM_ALLOWED_USER_IDS`, `TELERUBIK_MAX_FILE_SIZE_MB`, `TELERUBIK_POLL_SECONDS`
- `TELERUBIK_DRY_RUN=true` initially

Never commit `.env`, Telegram/Rubika session files, tokens, SQLite databases or downloaded media. Session files grant account access and must be handled like passwords. Do not place them in Git history or ordinary workflow artifacts.

## Run locally (Termux or Linux)

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
# Edit .env locally, keeping all secrets private
telerubik check
telerubik status
telerubik run
```

Dry-run downloads and validates files in an isolated temporary directory but does not upload them, claim queue entries, or mark them completed. Pending jobs remain available for a later live run. For a real test, first verify that the Rubpy session is authenticated and the destination is private; then set `TELERUBIK_DRY_RUN=false`. Do not enable live transfer until the pinned Rubpy API is verified.

## File-size and large-file behavior

The worker rejects files larger than `TELERUBIK_MAX_FILE_SIZE_MB` before downloading when Telegram provides the size, and validates the actual downloaded size before upload. The default is 2048 MiB (2 GiB).

The Rubika sender combines two inspected reference implementations:

- **Walrus-style normal upload:** one Rubika document is uploaded with `client.upload(...)`, then finalized with `client.send_message(file_inline=...)`. File metadata is normalized, media type is selected from the extension, transient finalization errors are retried, and a generic `File` finalization variant is available.
- **TG2RUB-style large-file handling:** files larger than 1.5 GiB are streamed into sequential ZIP archives of at most 500 MiB each, using `ZIP_STORED` to avoid CPU-heavy recompression. Each archive is uploaded and deleted before the next archive is generated, keeping temporary disk use bounded. A JSON manifest records the original filename, part order, sizes and SHA-256 hashes.

ZIP parts are separate Rubika messages; **Rubika does not merge them into one video automatically**. To reconstruct the original file, download all ZIP parts and the manifest into one directory, then run `telerubik.chunks.reassemble(manifest_path, parts_dir, output_path)`. The helper reads each ZIP member in order, validates every part and the full-file SHA-256, and only then publishes the reconstructed file. No manual extraction is required. This is a local reassembly step, not a server-side Rubika feature.

A failure mid-transfer stops the sequence; retrying the queue job may resend earlier parts. A 500 MiB part size is the reference project's strategy, not a guarantee of Rubika's current upload limit. A real private-destination test is still required before claiming end-to-end success.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

GitHub Actions can test code but cannot authenticate to your Telegram or Rubika accounts or prove a real transfer. A live test requires authorized session material and a private destination, which must never be posted in an issue or chat.

See [design notes](docs/design.md), [implementation checklist](docs/implementation-plan.md), and [security policy](SECURITY.md).

## Rubika session recovery

The remote worker never performs an interactive Rubika login. If it reports
`NotRegistered` while verifying the saved session, the stored authorization
must be refreshed locally. In Rubpy 7.3.5, `register_device()` is an authenticated
API request; upstream calls it after a successful `sign_in()`. Calling it with
the same rejected authorization cannot replace that authorization.

On the Termux device that owns the session, authenticate through Rubpy's normal
`start()` flow (it prompts for the phone number and one-time code only when
the saved authorization is rejected), then update the GitHub Actions secret
without printing the session contents:

```bash
cd ~/TeleRubik
python - <<'PY'
import asyncio
from rubpy import Client
from telerubik.adapters import _enable_verified_rubpy_transport, _resolve

async def main():
    client = Client(name="./private/rubika_session", platform="Android")
    try:
        await _resolve(client.connect())
        await _enable_verified_rubpy_transport(client)
        await _resolve(client.start())
        me = await _resolve(client.get_me())
        info = client.session.information()
        guid = getattr(getattr(me, "user", None), "user_guid", None)
        if not guid or not info or len(info) != 5 or info[2] != guid:
            raise RuntimeError("Rubika session verification failed; secret was not updated")
        print("Rubika account authentication verified.")
    finally:
        await _resolve(client.disconnect())

asyncio.run(main())
PY
chmod 600 private/rubika_session.rp
base64 private/rubika_session.rp | tr -d '\n' > "$HOME/rubika_session.b64"
gh secret set RUBIKA_SESSION_B64 --repo studio1111/TeleRubik < "$HOME/rubika_session.b64"
```

Do not paste the session file, Base64 value, phone code, or private key into chat
or workflow logs. Only dispatch the live transfer after the secret update succeeds.
