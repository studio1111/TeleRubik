# TeleRubik 🚚

**TeleRubik** transfers text-only posts and posts with media from Telegram chats you are authorized to read to a Rubika destination you control. It is independently implemented and borrows the upload pattern observed in Walrus: use a Rubpy **user-account Client**, upload the complete file with `client.upload`, then send the returned metadata with `client.send_message(file_inline=...)`. It does not use the Rubpy BotClient path for media uploads.

> **Status:** implementation and automated tests are being built on the feature branch. The Rubpy upload method must still be validated against the installed `rubpy==7.3.5` runtime, and a real end-to-end transfer to a private destination has not yet been performed.

## Current capabilities

- Telethon polling of configured Telegram chats, including text-only posts and media captions.
- Durable, idempotent SQLite queue keyed by source chat and message ID.
- Bounded retries and recovery of jobs interrupted while processing.
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

Dry-run downloads and validates files but does not upload them. For a real test, first verify that the Rubpy session is authenticated and the destination is private; then set `TELERUBIK_DRY_RUN=false`. Do not enable live transfer until the pinned Rubpy API is verified.


### Create the export on your authenticated device

After Rubpy has authenticated successfully on your own device and the client has been disconnected cleanly, export the database locally:

```bash
python scripts/export_rubika_session.py --source private/rubika_session.rp --output private/rubika_session.rp.b64
```

The exporter validates the Rubpy database and writes a base64 file with owner-only permissions. Open that local file on your device and copy its contents directly into the GitHub Actions secret editor. Never paste the encoded value into a terminal command, issue, chat, or workflow log. The exporter does not create an authenticated session; it only packages an existing one.

## Secure Rubpy session for GitHub Actions

Rubpy 7.3.5 stores a user session in a SQLite database with the `.rp` suffix (for example, `private/rubika_session.rp`). The session contains account-authentication material and must be treated as a password. Do not commit it, upload it as a normal artifact, or paste it into chat.

To provision GitHub Actions after creating and testing the Rubpy session locally:

1. Close the local Rubpy client cleanly so its SQLite database is flushed.
2. Base64-encode the complete `private/rubika_session.rp` file on the device where you authenticated. Keep the output private.
3. In the repository's **Settings → Secrets and variables → Actions**, create the repository secret `RUBIKA_SESSION_B64` with that base64 text as its value. Do not create a secret containing only the session name.
4. Run **Actions → Rubika session preflight → Run workflow**. The workflow restores the session into a private file, checks the SQLite integrity and Rubpy schema, then removes the temporary session file.

The preflight validates the stored session file's structure; it does not yet prove that Rubika accepts the session or that a message can be sent. A separate authenticated connection check and a private end-to-end transfer are still required before disabling dry-run.

The session restore helper reads `RUBIKA_SESSION_B64`, writes `private/rubika_session.rp` with owner-only permissions, and never prints session contents. The existing `RUBIKA_SESSION_NAME` setting remains a path/name, not a credential. The destination GUID is already configured separately as `RUBIKA_DESTINATION_GUID`.

## File-size and large-file behavior

The worker currently rejects files larger than `TELERUBIK_MAX_FILE_SIZE_MB` before downloading when Telegram provides the size, and validates the actual downloaded size before upload. The default is 2048 MiB (2 GiB). Do not assume that a large file can be sent merely by increasing this setting: actual Rubika server limits and Rubpy's upload behavior must be tested. The local chunk/manifest utility, where present, is not Rubika-native resumable upload and is not automatically sent as one reconstructed file.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

GitHub Actions can test code but cannot authenticate to your Telegram or Rubika accounts or prove a real transfer. A live test requires authorized session material and a private destination, which must never be posted in an issue or chat.

See [design notes](docs/design.md), [implementation checklist](docs/implementation-plan.md), and [security policy](SECURITY.md).


## Request a Rubika login code safely

If the first login attempt fails with `InvalidInput`, do not repeatedly request SMS codes. The Rubpy 7.3.5 `sendCode` method expects the phone number as digits without a leading plus sign. Use the helper below once after checking the number's international country code:

```bash
python scripts/request_rubika_code.py
```

Enter the number in international format (for example, `+<country-code><number>`). The helper normalizes Persian/Arabic digits and an international `00` prefix, uses the Android client profile, and reports only the API status and detail identifiers. It never prints the phone number, OTP, password, or raw server response. It only requests a code; it does not complete login or create an authenticated session.

If `INVALID_INPUT` persists with a correctly formatted international number, stop retrying and investigate the server/API response rather than switching platforms or repeatedly requesting codes.
