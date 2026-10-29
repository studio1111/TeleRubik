# TeleRubik design

## Goal
Move media from Telegram chats the operator is authorized to read into one configured Rubika destination, with durable local state and conservative handling of secrets and files.

## Flow
Telegram client (Telethon) -> source chat/user filters -> SQLite idempotent queue -> bounded download directory -> path/size checks -> Rubpy sender -> queue completion. Failures retry with bounded exponential backoff and eventually become failed.

## Boundaries
- config.py: environment parsing and validation.
- security.py: safe filenames, hashes and size limits.
- queue.py: durable queue and state transitions.
- engine.py: download/send orchestration and cleanup.
- adapters.py: Rubpy API boundary.
- cli.py: lifecycle and polling.

## Safety defaults
- Dry-run is on by default.
- No secrets or session files in Git.
- Download paths must remain inside the temporary directory.
- Oversize or unknown-size files fail closed at processing time.
- SQLite is local-first; Supabase is not required and no remote database is provisioned automatically.

## Known integration risk
Rubpy changes independently. Verify BotClient.send_document argument compatibility against the pinned version and test with a private destination before production use. The adapter raises an explicit error if the method is absent. Do not turn off dry-run until upload behavior is verified.
