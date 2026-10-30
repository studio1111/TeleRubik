# TeleRubik design

## Goal
Move text posts and supported downloadable media from Telegram chats the operator is authorized to read into one configured Rubika destination, with durable local state and conservative handling of secrets and files.

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
- The default local size ceiling is 2048 MiB (2 GiB), configurable downward; values above that ceiling are rejected.\n- Media is downloaded to a bounded local file path, not accumulated as one in-memory byte string. The worker validates actual on-disk size before upload.\n- The local size ceiling does not override Rubika server-side upload limits; large-file delivery remains unverified until a private-destination integration test succeeds.\n- Oversize or unknown-size files fail closed at processing time.
- SQLite is local-first; Supabase is not required and no remote database is provisioned automatically.

## Known integration risk
Rubpy changes independently. The adapter targets the documented BotClient.send_message and BotClient.send_file API in pinned Rubpy 7.3.5. Mocked adapter tests and CI do not prove authenticated network delivery; a controlled private-destination test is still required. Queue delivery is at-least-once: a crash after remote acceptance but before local completion can produce a duplicate on restart. Telegram albums are currently delivered as separate messages/files, and formatting/entities are not fully preserved. Keep dry-run enabled until private-destination testing is deliberately approved.
