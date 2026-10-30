# Rubika chunk-upload protocol compatibility

**Finding: a chunk protocol exists in the Rubino user-account API, but it is not the BotClient upload API used by TeleRubik.**

## Confirmed public implementation

The archived Rubpy source contains a separate Rubino client upload path. It:

1. Calls `requestUploadFile` with profile ID, file name, total file size, and file type.
2. Receives a file ID, server URL, and `hash_file_request`.
3. Uploads raw byte ranges in multiple HTTP requests with headers including `file-id`, `total-part`, `part-number`, `chunk-size`, and `hash-file-request`.
4. Returns the completed upload metadata after the final part.

A separate older Rubika client implementation also shows a multi-request upload flow using numbered parts and per-part hashes/access hashes. These are evidence of chunking in user-account APIs, not proof that the bot upload endpoint accepts the same protocol.

## Why this cannot be transplanted into TeleRubik's current sender

TeleRubik is configured with `RUBIKA_BOT_TOKEN` and `RUBIKA_DESTINATION_GUID`. Its pinned `rubpy==7.3.5` sender uses `BotClient.request_send_file` and `BotClient.upload_file`.

The BotClient path obtains one `upload_url`, then posts one multipart/form-data file field and returns one `file_id`; it does not expose the Rubino profile ID, request-upload response fields, per-part authentication/hash headers, or the user-account `requestUploadFile` method. The final message is sent using the bot API's `sendFile` method.

Therefore, sending Rubino-style chunk headers to a BotClient upload URL would be an undocumented protocol guess and could corrupt or orphan uploads. The Rubino protocol requires user-account authorization/session material and must not be silently enabled using a bot token.

## Safe next implementation paths

- **Keep the bot-token architecture:** only implement numbered independent part files plus a manifest/reassembly utility if the product owner explicitly accepts that recipients must download all parts and reconstruct the original file. This is application-level multipart delivery, not one native Rubika video attachment.
- **Use the Rubino user-account API:** this requires a separately authorized Rubika user session and a dedicated adapter. It must be evaluated as a distinct authentication model, with explicit account-owner approval, isolated secrets, tests for upload resume/integrity/finalization, and a private-destination integration run. Do not reuse or infer a user session from the bot token.
- Do not claim a native single-message assembled upload until a private integration test confirms that the destination exposes the expected final file and its SHA-256 matches the source.

## References

- Rubpy BotClient methods: https://rubpy.shayan-heidari.ir/bot_client_methods/
- Rubpy BotClient implementation: https://github.com/shayanheidari01/rubika/blob/master/rubpy/bot/bot.py
- Rubpy Rubino client implementation (chunk loop): https://github.com/shayanheidari01/rubika/blob/master/rubpy/rubino/client.py
- Historical Rubika client implementation showing numbered parts: https://github.com/rubikabot/post/blob/main/api_rubika.py

## Execution status

No upload has been performed. The current GitHub integration can write repository files and inspect workflow runs, but cannot dispatch GitHub Actions. No Rubika user-account session is configured in the repository's environment template. The bot-token route therefore cannot safely perform the documented Rubino chunk protocol from the available configuration.
