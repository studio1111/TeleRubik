# TeleRubik implementation plan

- [x] Establish repository policy, secret handling and initial package layout.
- [x] Implement validated environment configuration, 2 GiB local file-size ceiling, and dry-run default.
- [x] Implement filename and size safety helpers.
- [x] Implement durable idempotent SQLite queue and retry states.
- [x] Implement bounded transfer orchestration and temporary-file cleanup.
- [x] Add Telegram polling and Rubpy adapter boundary, including streaming incremental history and oldest-first bounded initial import.
- [x] Add unit tests and CI, CodeQL and Dependabot configuration.
- [x] Verify the documented Rubpy 7.3.5 send_message/send_file API and media type values.
- [ ] Confirm Rubika server-side maximum upload size and timeout behavior for files approaching 2 GiB.\n- [ ] Run a staged private-destination upload test with a representative large file, verifying integrity and cleanup.\n- [ ] Run an end-to-end transfer to a private test chat before production use.
- [x] Document the local-first architecture, safe dry-run workflow, and known delivery limitations.

No credentials, session files, or user content belong in this repository.
