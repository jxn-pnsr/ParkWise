"""In-process rate limiter (M2). No extra service needed.

    hit("login-ip", client_ip(), limit=20, window=300)  # raises 429

    Counts are per worker, so the effective limit is limit x workers.
    Disable with RATE_LIMIT_ENABLED=0.
"""
import threading
import time
from collections import deque

from flask import current_app, has_app_context

from core.errors import AppError

_hits = {}
_lock = threading.Lock()
_MAX_KEYS = 50_000


class TooManyRequests(AppError):
    status = 429
    code = "TOO_MANY_REQUESTS"
    message = "Too many attempts. Please wait a moment and try again."


def _enabled():
    return not has_app_context() or current_app.config.get("RATE_LIMIT_ENABLED", True)


def hit(bucket, key, limit, window, message=None):
    """Track attempts and raise 429 when the limit is exceeded."""
    if not _enabled() or key is None:
        return
    now = time.monotonic()
    k = (bucket, str(key).lower())
    with _lock:
        q = _hits.get(k)
        if q is None:
            if len(_hits) >= _MAX_KEYS:
                _prune(now)
            q = _hits[k] = deque()
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = int(window - (now - q[0])) + 1
            raise TooManyRequests(message=message or TooManyRequests.message, extra={"retry_after_seconds": retry})
        q.append(now)


def _prune(now, max_age=3600):
    for k in [k for k, q in _hits.items() if not q or now - q[-1] > max_age]:
        del _hits[k]


def reset():
    """Clear stored hits for tests."""
    with _lock:
        _hits.clear()