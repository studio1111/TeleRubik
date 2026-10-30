from __future__ import annotations

class PermanentTransferError(RuntimeError):
    """A transfer failure that cannot succeed by retrying the same request."""

def http_status_code(error: BaseException) -> int | None:
    """Extract an HTTP status from common client exception/response shapes."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for candidate in (current, getattr(current, "response", None)):
            if candidate is None:
                continue
            for name in ("status", "status_code"):
                value = getattr(candidate, name, None)
                try:
                    if value is not None:
                        return int(value)
                except (TypeError, ValueError):
                    pass
        current = current.__cause__ or current.__context__
    return None
