from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    pass


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
    rubika_session_name: str
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
        required = (
            "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SOURCE_CHATS",
            "RUBIKA_SESSION_NAME", "RUBIKA_DESTINATION_GUID",
        )
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
            allowed = frozenset(
                int(item.strip())
                for item in values.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
                if item.strip()
            )
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_ALLOWED_USER_IDS must contain numeric IDs") from exc
        session_name = values["RUBIKA_SESSION_NAME"].strip()
        if not session_name or Path(session_name).name in {"", ".", ".."}:
            raise ConfigurationError("RUBIKA_SESSION_NAME must be a valid session name or path")
        max_mb = _positive_int(values, "TELERUBIK_MAX_FILE_SIZE_MB", 2048)
        return cls(
            telegram_api_id=api_id,
            telegram_api_hash=values["TELEGRAM_API_HASH"].strip(),
            telegram_session_path=Path(values.get("TELEGRAM_SESSION_PATH", "./private/telegram_session")),
            telegram_session_string=values.get("TELEGRAM_SESSION_STRING", "").strip(),
            telegram_source_chats=chats,
            rubika_session_name=session_name,
            rubika_destination_guid=values["RUBIKA_DESTINATION_GUID"].strip(),
            state_db=Path(values.get("TELERUBIK_STATE_DB", "./data/telerubik.sqlite3")),
            download_dir=Path(values.get("TELERUBIK_DOWNLOAD_DIR", "./downloads")),
            max_file_size_bytes=max_mb * 1024 * 1024,
            max_retries=_positive_int(values, "TELERUBIK_MAX_RETRIES", 3),
            retry_base_seconds=_positive_int(values, "TELERUBIK_RETRY_BASE_SECONDS", 2),
            poll_seconds=_positive_int(values, "TELERUBIK_POLL_SECONDS", 20),
            lookback_limit=_positive_int(values, "TELERUBIK_LOOKBACK_LIMIT", 25),
            allowed_user_ids=allowed,
            dry_run=values.get("TELERUBIK_DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"},
        )
