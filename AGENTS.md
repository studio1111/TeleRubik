# TeleRubik repository instructions

- Inspect related code and tests before changing behavior; keep changes small and testable.
- Never commit secrets, Telegram session files, Rubika sessions, real tokens, or private chat exports. Use .env.example placeholders only.
- Treat message metadata, filenames, captions, and remote responses as untrusted input.
- Keep external clients behind adapter boundaries and core logic testable without network access.
- Add tests before behavior changes and run the full suite; never claim unrun tests passed.
- Pin direct dependencies. Use least-privilege CI permissions.
- Do not copy source from TG2RUB or Tele2Rub; independently implement documented behavior.
- Target Python 3.11+ and support Termux/Linux where dependencies permit.
- Any future Supabase integration must be optional, server-side for secret keys, and protected by RLS. SQLite remains the default.
