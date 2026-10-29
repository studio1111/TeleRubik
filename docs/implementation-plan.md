# TeleRubik implementation plan

- [x] Establish repository policy, secret handling and initial package layout.
- [x] Implement validated environment configuration and dry-run default.
- [x] Implement filename and size safety helpers.
- [x] Implement durable idempotent SQLite queue and retry states.
- [x] Implement bounded transfer orchestration and temporary-file cleanup.
- [x] Add Telegram polling and Rubpy adapter boundary.
- [x] Add unit tests and CI, CodeQL and Dependabot configuration.
- [ ] Verify exact Rubpy send_document signature against the pinned runtime.
- [ ] Run an end-to-end transfer to a private test chat before production use.
- [ ] Add operational service templates after upload flow verification.

No credentials, session files, or user content belong in this repository.
