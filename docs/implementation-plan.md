# TeleRubik implementation plan

- [x] Establish repository policy, secret handling and initial package layout.
- [x] Implement validated environment configuration and dry-run default.
- [x] Implement filename and size safety helpers.
- [x] Implement durable idempotent SQLite queue and retry states.
- [x] Implement bounded transfer orchestration and temporary-file cleanup.
- [x] Implement Telegram polling and a Rubpy user-session upload adapter.
- [x] Use the Walrus-style flow: upload complete file, then send returned metadata with the original caption.\n- [x] Support text-only posts and media captions.\n- [x] Persist failed-upload downloads with SHA-256 sidecars and verify them before reuse.
- [x] Add adapter tests for upload/send order, empty captions, missing metadata and missing files.
- [x] Document the user-session requirement and keep secrets/session files out of Git.
- [x] Keep live sending disabled by default.
- [ ] Run CI on Python 3.11, 3.12 and 3.13 and fix any failures. (Not yet confirmed.)
- [ ] Verify exact behavior against installed rubpy==7.3.5, including response-level errors and large files.
- [ ] Run an end-to-end transfer to a private Rubika destination using an authorized session.
- [ ] Add persistent worker hosting only after runtime behavior and session storage are validated.

## Explicit boundaries

The upload flow is inspired by inspected open-source implementations but is independently implemented. A successful upload call is not proof that Rubika accepted the final message. Credentials and session files must never be committed or uploaded as ordinary artifacts. CI cannot perform a real account transfer without an authorized session and private destination.

No credentials, session files, or user content belong in this repository.
