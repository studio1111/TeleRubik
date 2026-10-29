# TeleRubik 🚚

**TeleRubik** is an independently implemented, local-first worker for transferring media from Telegram chats you are authorized to read to a configured Rubika destination. It includes a durable SQLite queue, bounded retries, file-size checks, temporary-file cleanup, and CI security checks.

> **Status:** initial implementation scaffold. The Rubpy upload API still needs runtime verification against the pinned package and an end-to-end test to a private destination. No real transfer has been run from this environment.

## Features
- Telegram source polling through Telethon.
- Idempotent queue keyed by Telegram chat and message ID.
- SQLite state survives restarts; failed jobs have bounded retries.
- Safe filename normalization, size cap, download path boundary checks and temporary-file cleanup.
- Dry-run enabled by default; configuration checks never print secrets.
- Unit tests, GitHub Actions CI, CodeQL and pip-audit workflow.
- Local-first by default; Supabase is not required and no cloud project is created.

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
Fill required values in `.env`. Never share or commit it. Telegram session data is highly sensitive and is excluded by `.gitignore`.

## First run
```bash
telerubik check
telerubik status
telerubik run
```
`TELERUBIK_DRY_RUN=true` is the safe default. It downloads and validates files but does not upload them. After verifying the Rubpy upload method and settings, set it to `false` and test only with a private destination. Stop with Ctrl+C.

## Configuration
See `.env.example`. Required variables: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SOURCE_CHATS`, `RUBIKA_BOT_TOKEN`, and `RUBIKA_DESTINATION_GUID`. Source chats are comma-separated. `TELEGRAM_ALLOWED_USER_IDS` optionally filters sender IDs; an empty value means no sender filter, so configure only trusted source chats.

## Tests
```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Security notes
- Use only sources you are permitted to access and destinations you control or are authorized to post to.
- Do not commit `.env`, Telegram session files, Rubika tokens, databases, or downloaded media.
- The current Rubpy adapter is defensive about API mismatches. Verify `BotClient.send_document` against the pinned release before disabling dry-run.
- GitHub Actions tests the core but does not authenticate to Telegram or Rubika.

See [design notes](docs/design.md), [implementation checklist](docs/implementation-plan.md), and [security policy](SECURITY.md).
