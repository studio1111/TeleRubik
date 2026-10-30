# Large-file and multipart upload investigation

**Status: not enabled; protocol verification required.**

## What the pinned Rubpy interface exposes

The Rubpy 7.3.5 BotClient documentation describes this sequence:

1. `request_send_file(type)` obtains an upload URL.
2. `upload_file(url, file_name, file_path)` uploads one local file and returns a `file_id`.
3. `send_file(chat_id, file=...)` composes those operations and sends the resulting file.

The documented `upload_file` parameters do not expose a chunk size, part number, total-part count, resumable session, or finalization/assembly operation. The documented `send_file` API likewise accepts one file path or an existing `file_id`.

The upstream Rubpy source currently visible in its public repository implements `upload_file` using one `aiohttp.FormData` field named `file` and one POST to the upload URL. That is multipart/form-data encoding, **not** a multi-request chunked/resumable upload protocol. The source also does not expose a chunk loop or an API to assemble independently uploaded pieces. The upstream repository is archived, so this must be rechecked against the exact installed 7.3.5 wheel before relying on undocumented behavior.

References:
- Rubpy BotClient methods: https://rubpy.shayan-heidari.ir/bot_client_methods/
- Rubpy 7.3.5 package: https://pypi.org/project/rubpy/7.3.5/
- Upstream source repository: https://github.com/shayanheidari01/rubika/

## Important terminology

- **multipart/form-data**: one HTTP request whose body contains form fields and a file part. This is what the documented Rubpy upload path describes.
- **chunked/resumable upload**: multiple requests upload numbered byte ranges/parts into one server-side upload session, followed by a server-recognized completion/assembly step.
- **split files**: several independent files (for example `.part001`, `.part002`). This does not recreate the original media in Rubika unless a receiver downloads every part and concatenates them in the exact order.

These mechanisms are not interchangeable. TeleRubik must not silently send independent fragments as if they were one playable video.

## Safe experiment plan

1. Confirm the exact installed Rubpy 7.3.5 source/signatures and inspect its upload implementation.
2. Run a controlled, private-destination size probe using a generated harmless binary first. Record HTTP status, response shape, upload duration, and returned file identifier. Do not log tokens, signed upload URLs, or private destination identifiers.
3. If the native upload accepts the full file, use the existing `send_file` route for the exact scoped Telegram message.
4. If the server rejects it (for example HTTP 413), do not retry the same request and do not split it into unrelated Rubika messages.
5. Only implement true multipart chunks after obtaining a documented or experimentally verified protocol for upload-session creation, part numbering, byte ranges, total size, integrity checks, retry semantics, and finalization.
6. After the protocol is proven, add unit tests for boundary sizes, final short chunk, interrupted/retried parts, ordering, checksum, cleanup, and no-send-on-incomplete-upload, then add a guarded private-destination integration workflow.

## Current TeleRubik behavior

- The sender currently calls `BotClient.send_file` once for each media job.
- HTTP 413 is classified as a permanent error, so it is not retried as a transient failure.
- The engine reuses one validated local download across transient retries and removes it afterward.
- Dry-run remains enabled by default.
- The current GitHub integration can edit repository files and inspect workflow runs, but cannot dispatch a `workflow_dispatch` run. No live upload has been performed from this environment.

## Decision

Do not add a fake multipart mode or automatically split a video into unrelated files. First establish whether the pinned Rubpy package or Rubika upload endpoint has a real assembly protocol. Until then, the correct production behavior is a clear permanent failure on a confirmed size rejection, with the local source file preserved only for the duration of the attempt and cleaned afterward.
