"""Shared FastAPI dependencies: authentication and rate limiting.

Both are deliberately small. The API key is an optional shared secret for when
the POC is exposed beyond localhost, and the limiter is an in-process token
bucket rather than a Redis-backed one, because a single-process POC gains
nothing from the extra dependency.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import Request

from backend.app.config import Settings, get_settings
from backend.app.core.errors import RateLimitedError, UnauthorizedError

API_KEY_HEADER = "X-API-Key"


def require_api_key(request: Request) -> None:
    """Enforce the shared secret when one is configured.

    With ``ATELIER_API_KEY`` unset this is a no-op, which keeps local
    development and the Streamlit UI frictionless.
    """
    expected = get_settings().api_key
    if not expected:
        return

    presented = request.headers.get(API_KEY_HEADER, "")
    # Compare in constant time; a short-circuiting == leaks the prefix length.
    if not _constant_time_equals(presented, expected):
        raise UnauthorizedError("API key missing or incorrect.")


def _constant_time_equals(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    mismatch = 0
    for a, b in zip(left, right, strict=True):
        mismatch |= ord(a) ^ ord(b)
    return mismatch == 0


class TokenBucketLimiter:
    """Fixed-window counter per client, reset every 60 seconds."""

    WINDOW_SECONDS = 60.0

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._lock = Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, client_key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS

        with self._lock:
            recent = [stamp for stamp in self._hits[client_key] if stamp > cutoff]
            if len(recent) >= self._limit:
                self._hits[client_key] = recent
                raise RateLimitedError(f"{client_key} exceeded {self._limit} requests/minute.")
            recent.append(now)
            self._hits[client_key] = recent

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter: TokenBucketLimiter | None = None


def get_limiter(settings: Settings | None = None) -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        settings = settings or get_settings()
        _limiter = TokenBucketLimiter(settings.rate_limit_per_minute)
    return _limiter


def reset_limiter() -> None:
    """Clear limiter state between tests."""
    global _limiter
    _limiter = None


def enforce_rate_limit(request: Request) -> None:
    client_key = request.client.host if request.client else "unknown"
    get_limiter().check(client_key)


def guard(request: Request) -> None:
    """Single dependency applied to the whole ``/api`` router."""
    require_api_key(request)
    enforce_rate_limit(request)
