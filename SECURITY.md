# Security policy

Do not publish secrets, session files, private chat data, or exploitable details in public issues. Use GitHub private vulnerability reporting where available.

- Keep .env, Telegram sessions, Rubika credentials, databases, and downloads out of version control.
- Rotate tokens immediately if exposed. Never put secrets in CI logs.
- Only monitor Telegram chats you are authorized to access.
- Keep file-size limits conservative and temporary files on storage you control.
- Dry-run is enabled by default; test against a private destination before enabling sends.
