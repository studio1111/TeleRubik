# TeleRubik 🚚

**TeleRubik** is a local-first worker for transferring posts and downloadable media from Telegram chats you are authorized to read to a configured Rubika destination. It uses a durable SQLite queue, bounded retries, file-size checks, temporary-file cleanup, and CI security checks.

> **Status:** Text/photo/video/file routing is implemented. The Rubpy 7.3.5 BotClient.send_message and BotClient.send_file signatures were checked against the official documentation. CI, dependency audit, and CodeQL pass for the current feature branch. No authenticated end-to-end transfer to Rubika has been run from this environment, so keep dry-run enabled until testing with a private destination.

## Features
- Telegram source polling through Telethon.
- Transfers text-only posts as text and downloadable photos, videos, and generic files with their captions.
- If a Telegram message contains a non-downloadable preview or unsupported media but also has text, transfers the text instead of repeatedly retrying an impossible file download.
- Enqueues each polling batch oldest-first and processes durable jobs in queue order.
- Persists a per-source scan cursor: the initial import is limited to the configured recent-history window, then polling catches every newer message after downtime instead of relying on a rolling last-N window. Incremental history is streamed to avoid buffering an unbounded backlog in memory.
- Idempotent queue keyed by Telegram chat and message ID.
- SQLite state survives restarts; failed jobs have bounded retries.
- Safe filename normalization, size cap, download path boundary checks, and temporary-file cleanup.
- Dry-run enabled by default; invalid dry-run values are rejected, and configuration checks never print secrets.
- Unit tests, GitHub Actions CI, CodeQL, and pip-audit workflow.
- Local-first by default. Supabase is not required and no cloud project is created.

## Current limitations
- Telegram albums are transferred as separate messages/files, not as one native grouped album.
- Text formatting/entities and original Telegram message metadata are not fully reproduced on Rubika.
- Unsupported media with no text is skipped; messages exceeding the configured size cap are skipped.
- CI tests do not authenticate to Telegram or Rubika. A controlled private-destination test is still required before live use.

## Requirements
- Python 3.11+
- Telegram API ID and API hash from https://my.telegram.org
- Rubika bot token and destination chat identifier accepted by Rubpy
- Access to the Telegram chats you configure

## Setup (Termux or Linux)
```bash
# Termux only: pkg update && pkg install python git -y
# Linux: install Python 3.11+ and Git first
git clone https://github.com/studio1111/TeleRubik.git
cd TeleRubik
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
nano .env
```

Fill required values in .env. Never share or commit it. Telegram session data is highly sensitive and is excluded by .gitignore.

## First run
```bash
telerubik check
telerubik status
telerubik run
```

Keep TELERUBIK_DRY_RUN=true for the first run. In this mode the worker scans and queues eligible messages but does not process the queue or send anything. This preserves jobs for later use. After reviewing the queue and settings, set it to false only for a controlled test to a private destination you control. Stop with Ctrl+C.

## Configuration
See .env.example. Required variables: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SOURCE_CHATS, RUBIKA_BOT_TOKEN, and RUBIKA_DESTINATION_GUID. Source chats are comma-separated. TELEGRAM_ALLOWED_USER_IDS optionally filters sender IDs; an empty value means no sender filter, so configure only trusted source chats.

## Tests
```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Remote GitHub Actions transfer (phone-free media storage)

A manual workflow, `.github/workflows/remote-single-transfer.yml`, runs the one-message transfer on an ephemeral GitHub-hosted Ubuntu runner. The media file is downloaded only to that remote runner, never to the phone. The workflow is pinned to the approved `@Animehighbo` message `1734` and its exact Spider-Man filename; it cannot scan or drain the general queue. Dry-run is the default. A live send requires deliberately enabling the workflow input `allow_live_send`.

Configure these repository Actions secrets before running it: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` (Telethon StringSession), `RUBIKA_BOT_TOKEN`, and `RUBIKA_DESTINATION_GUID`. The StringSession is sensitive authentication material and must only be stored as a GitHub secret. The runner deletes its temporary download, SQLite state, and workspace session artifacts when the job ends. GitHub-hosted runner disk and job-time limits still apply; this is not an unlimited storage service.

To export the existing authorized Telethon session once on the current Termux setup (this prints a secret; do not paste it into chat or commit it):

```bash
python -c 'from dotenv import load_dotenv; load_dotenv(); import os; from telethon.sync import TelegramClient; from telethon.sessions import StringSession; c=TelegramClient(os.getenv("TELEGRAM_SESSION_PATH", "./private/telegram_session"), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"]); c.connect(); print(StringSession.save(c.session)); c.disconnect()'
```

Copy the printed value directly into the GitHub repository secret named `TELEGRAM_SESSION_STRING`, then clear it from clipboard/history. No media is downloaded by this export step.

## Isolated one-message transfer

For a controlled test, use `transfer-one` with an exact source chat, Telegram message ID, and expected filename. This command does not scan the channel or drain the general queue; it can only claim the selected source/message pair. It also checks the Telegram file size and available disk space before downloading.

Example (keep dry-run enabled for the first pass):

```bash
telerubik transfer-one --source-chat @animehighbo --message-id 1734 --expected-filename "Spider-Man.Brand.New.Day.2026.720p.WEB-DL.ValaMovie.mkv"
```

With `TELERUBIK_DRY_RUN=true`, it downloads and validates only that file, then leaves its queue item pending without sending. A live run requires deliberately setting dry-run to false; the same command remains restricted to that one message. The local 2048 MiB cap does not guarantee that Rubika accepts files of that size. The actual server-side upload limit and timeout behavior must be established by a successful private-destination file test.

## Security notes
- Use only sources you are permitted to access and destinations you control or are authorized to post to.
- Do not commit .env, Telegram session files, Rubika tokens, databases, or downloaded media.
- The adapter follows the documented Rubpy 7.3.5 API, but network delivery and destination permissions must still be verified with a private test.
- GitHub Actions tests the core and adapter routing without authenticating to Telegram or Rubika.

See [design notes](docs/design.md), [implementation checklist](docs/implementation-plan.md), [Termux validation and smoke-test runbook](docs/termux-smoke-test.md), and [security policy](SECURITY.md).