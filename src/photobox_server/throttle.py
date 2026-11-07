"""Per-device upload limit. Counts are in memory, so they reset when the server restarts."""
from __future__ import annotations
import os
import threading
import time
from collections import defaultdict, deque

WINDOW_SECONDS = 60
_lock = threading.Lock()
_uploads: defaultdict[str, deque[float]] = defaultdict(deque)


def limit_per_minute() -> int:
    return int(os.getenv("PHOTOBOX_MAX_UPLOADS_PER_MINUTE", "30"))


def retry_after(device_id: str, limit: int | None = None) -> int | None:
    """Record an attempt. Returns None if allowed, otherwise the seconds to wait."""
    now = time.monotonic()
    limit = limit_per_minute() if limit is None else limit
    with _lock:
        attempts = _uploads[device_id]
        while attempts and attempts[0] <= now - WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= limit:
            return max(1, int(attempts[0] + WINDOW_SECONDS - now) + 1) if attempts else WINDOW_SECONDS
        attempts.append(now)
        return None


def reset() -> None:
    with _lock:
        _uploads.clear()
