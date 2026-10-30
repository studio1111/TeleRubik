# TeleRubik implementation plan

- [x] Establish repository policy, secret handling and initial package layout.
- [x] Implement validated environment configuration and dry-run default.
- [x] Implement filename and size safety helpers.
- [x] Implement durable idempotent SQLite queue and retry states.
- [x] Implement bounded transfer orchestration and temporary-file cleanup.
- [x] Implement Telegram polling and a Rubpy user-session upload adapter.
- [x] Use the Walrus-style user-account flow: upload complete file, then send returned metadata with the original caption.
- [x] Support text-only posts and media captions.
- [x] Persist failed-upload downloads with SHA-256 sidecars and verify them before reuse.
- [x] Validate the local authenticated Rubpy SQLite session before non-interactive worker startup.
- [x] Suppress Rubpy session logging and keep raw API exception messages out of persistent queue state.
- [x] Persist monotonic per-source Telegram cursors so subsequent scans do not stop at the initial lookback window.
- [x] Add adapter tests for upload/send order, empty captions, missing metadata and missing files.
- [x] Document the user-session requirement and keep secrets/session files out of Git.
- [x] Keep live sending disabled by default.
- [x] Make dry-run previews non-destructive: keep jobs pending, avoid retry-count changes, and isolate preview downloads from retained live-retry cache.
- [x] Run CI on Python 3.11, 3.12 and 3.13 and fix any failures (latest checked head passed all jobs).
- [x] Inspect the pinned rubpy==7.3.5 source contract: SQLite session schema, restored auth fields, AES passphrase derivation, 1 MiB upload streaming, Builder status validation and the returned Update(data) shape.
- [x] Adapt the sender to Rubpy 7.3.5 response semantics, including explicit outer status-pair checks and message-ID validation for successful Update payloads.
- [x] Replace Rubpy 7.3.5's certificate-verification-disabled aiohttp session with a certificate-verified TLS session before the first authenticated API request.
- [ ] Run a live runtime smoke test with the authorized session and private Rubika destination.
- [ ] Run an end-to-end transfer to a private Rubika destination using an authorized session.
- [ ] Add persistent worker hosting only after runtime behavior and session storage are validated.

## Explicit boundaries

The upload flow is inspired by inspected open-source implementations but is independently implemented. A successful upload call is not proof that Rubika accepted the final message. Credentials and session files must never be committed or uploaded as ordinary artifacts. CI cannot perform a real account transfer without an authorized session and private destination.

No credentials, session files, or user content belong in this repository.
