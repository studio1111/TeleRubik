from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

class ConfigurationError(ValueError):
    pass


MAX_FILE_SIZE_MB = 2048

def _bool_value(env: dict[str, str], key: str, default: str) -> bool:
    value = env.get(key, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{key} must be a boolean (true/false)")


def _positive_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(env.get(key, str(default)).strip())
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{key} must be greater than zero")
    return value

@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_path: Path
    telegram_session_string: str
    telegram_source_chats: tuple[str, ...]
    rubika_bot_token: str
    rubika_destination_guid: str
    state_db: Path
    download_dir: Path
    max_file_size_bytes: int
    max_retries: int
    retry_base_seconds: int
    poll_seconds: int
    lookback_limit: int
    allowed_user_ids: frozenset[int]
    dry_run: bool

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        values = dict(os.environ if env is None else env)
        required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SOURCE_CHATS", "RUBIKA_BOT_TOKEN", "RUBIKA_DESTINATION_GUID")
        missing = [key for key in required if not values.get(key, "").strip()]
        if missing:
            raise ConfigurationError("Missing required configuration: " + ", ".join(missing))
        try:
            api_id = int(values["TELEGRAM_API_ID"])
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_API_ID must be an integer") from exc
        if api_id <= 0:
            raise ConfigurationError("TELEGRAM_API_ID must be greater than zero")
        chats = tuple(item.strip() for item in values["TELEGRAM_SOURCE_CHATS"].split(",") if item.strip())
        if not chats:
            raise ConfigurationError("TELEGRAM_SOURCE_CHATS must contain at least one chat")
        try:
            allowed = frozenset(int(item.strip()) for item in values.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if item.strip())
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_ALLOWED_USER_IDS must contain numeric IDs") from exc
        max_mb = _positive_int(values, "TELERUBIK_MAX_FILE_SIZE_MB", MAX_FILE_SIZE_MB)
        if max_mb > MAX_FILE_SIZE_MB:
            raise ConfigurationError(f"TELERUBIK_MAX_FILE_SIZE_MB cannot exceed {MAX_FILE_SIZE_MB} MiB")
        return cls(api_id, values["TELEGRAM_API_HASH"].strip(), Path(values.get("TELEGRAM_SESSION_PATH", "./private/telegram_session")), values.get("TELEGRAM_SESSION_STRING", "").strip(), chats,
                   values["RUBIKA_BOT_TOKEN"].strip(), values["RUBIKA_DESTINATION_GUID"].strip(),
                   Path(values.get("TELERUBIK_STATE_DB", "./data/telerubik.sqlite3")), Path(values.get("TELERUBIK_DOWNLOAD_DIR", "./downloads")),
                   max_mb * 1024 * 1024, _positive_int(values, "TELERUBIK_MAX_RETRIES", 3), _positive_int(values, "TELERUBIK_RETRY_BASE_SECONDS", 2),
                   _positive_int(values, "TELERUBIK_POLL_SECONDS", 20), _positive_int(values, "TELERUBIK_LOOKBACK_LIMIT", 25), allowed,
                   _bool_value(values, "TELERUBIK_DRY_RUN", "true"))
