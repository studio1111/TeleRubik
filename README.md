# TeleRubik 🚚

**TeleRubik** transfers text-only posts and posts with media from Telegram chats you are authorized to read to a Rubika destination you control. It is independently implemented and borrows the upload pattern observed in Walrus: use a Rubpy **user-account Client**, upload the complete file with `client.upload`, then send the returned metadata with `client.send_message(file_inline=...)`. It does not use the Rubpy BotClient path for media uploads.

> **Status:** implementation and automated tests are being built on the feature branch. The Rubpy upload method must still be validated against the installed `rubpy==7.3.5` runtime, and a real end-to-end transfer to a private destination has not yet been performed.

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

The worker currently rejects files larger than `TELERUBIK_MAX_FILE_SIZE_MB` before downloading when Telegram provides the size, and validates the actual downloaded size before upload. The default is 2048 MiB (2 GiB). Do not assume that a large file can be sent merely by increasing this setting: actual Rubika server limits and Rubpy's upload behavior must be tested. The local chunk/manifest utility, where present, is not Rubika-native resumable upload and is not automatically sent as one reconstructed file.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

GitHub Actions can test code but cannot authenticate to your Telegram or Rubika accounts or prove a real transfer. A live test requires authorized session material and a private destination, which must never be posted in an issue or chat.

See [design notes](docs/design.md), [implementation checklist](docs/implementation-plan.md), and [security policy](SECURITY.md).
