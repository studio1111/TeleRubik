# Termux validation and private-destination smoke test

This runbook is designed for Android Termux. It never asks you to paste API credentials, bot tokens, or session files into chat.

## 1. Update the working copy

From Termux:

```bash
cd ~/TeleRubik
git status --short
```

If this shows local edits you need, preserve them before switching branches. Then:

```bash
git fetch origin
git checkout feat/complete-post-transfer
git pull --ff-only origin feat/complete-post-transfer
python -m pip install -e .
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q src tests
```

## 2. Validate configuration without sending

Ensure the local `.env` has the intended source chat and destination. Do not print or share the file.

For this setup, the source should be `@Animehighbo`. Confirm the destination GUID locally; this runbook does not replace it or change any credentials.

```bash
telerubik check
telerubik status
```

Keep `TELERUBIK_DRY_RUN=true`. Run the worker and observe its logs:

```bash
telerubik run
```

Stop it with Ctrl+C. In dry-run mode, the worker scans Telegram and queues eligible text/media, but it does not process the queue or send to Rubika. Check the queue again with `telerubik status`.

## 3. Live smoke test (only after deliberate approval)

A live test is not part of CI and must be run locally because it requires your authenticated Telegram session and Rubika bot token.

- Use a private Rubika test destination that you control, not a public channel.
- Confirm the configured Telegram source is the intended chat.
- Keep a copy of the current `.env` in your private local storage only; never commit or share it.
- Change only the destination to the private test destination and set `TELERUBIK_DRY_RUN=false` locally after you explicitly decide to test.
- Start `telerubik run` and verify a single harmless text post first. Then verify one small image/file if needed.
- Stop the worker immediately after the test, restore `TELERUBIK_DRY_RUN=true`, and confirm with `telerubik check`.
- Check `telerubik status` and verify the post arrived correctly. Do not test with confidential or copyrighted material you are not authorized to transfer.

## Important behavior and limitations

- On the first scan, only the configured recent-history window is imported. After that, a durable per-source cursor lets polling catch all newer message IDs after downtime.
- Text, photos, videos, and downloadable files are supported. Telegram albums are currently delivered as separate Rubika messages/files.
- Formatting/entities and all original Telegram metadata are not fully reproduced.
- Delivery is at-least-once. If the process crashes after Rubika accepts a post but before SQLite records completion, a duplicate can occur after restart.
- CI, dependency audit, and CodeQL cannot prove live Telegram/Rubika delivery. Do not treat green checks as an authenticated end-to-end test.
