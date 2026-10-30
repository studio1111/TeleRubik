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
- adapters.py: Rubpy user-account API boundary, authenticated-session preflight and upload/send flow.
- cli.py: lifecycle and polling.

## Safety defaults
- Dry-run is on by default.
- No secrets or session files in Git.
- Download paths must remain inside the temporary directory.
- Oversize or unknown-size files fail closed at processing time.
- SQLite is local-first; Supabase is not required and no remote database is provisioned automatically.

## Known integration risk
Rubpy changes independently. The adapter targets the pinned user-account Client API: authenticate an existing SQLite session, call `Client.upload(...)`, then pass the returned file metadata to `Client.send_message(file_inline=...)`. Tests validate the adapter contract, session preflight and failure behavior, but they do not prove a real network upload. Keep dry-run enabled until a private-destination end-to-end test confirms text and media delivery.


## Incremental source scanning
- The first scan imports only the configured recent-history window, oldest-first.
- Each source has a durable, monotonically increasing Telegram message-ID cursor in SQLite.
- Later scans stream every message newer than that cursor, so a long worker outage or more messages than the first-run lookback window does not silently skip history.
- The cursor advances only after a full scan batch. If Telegram iteration fails part-way through, the batch is retried next time and the unique source/message key prevents duplicate queue entries.
