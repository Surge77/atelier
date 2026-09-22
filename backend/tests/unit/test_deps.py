"""API key check and rate limiter."""

from __future__ import annotations

import pytest

from backend.app.api.deps import TokenBucketLimiter, require_api_key
from backend.app.config import get_settings
from backend.app.core.errors import RateLimitedError, UnauthorizedError


class _FakeRequest:
    """Minimal stand-in; `require_api_key` only reads headers."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


class TestRequireApiKey:
    def test_allows_everything_when_unconfigured(self) -> None:
        require_api_key(_FakeRequest())  # type: ignore[arg-type]

    def test_accepts_matching_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATELIER_API_KEY", "correct-horse-battery")
        get_settings.cache_clear()

        require_api_key(_FakeRequest({"X-API-Key": "correct-horse-battery"}))  # type: ignore[arg-type]

    def test_rejects_wrong_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATELIER_API_KEY", "correct-horse-battery")
        get_settings.cache_clear()

        with pytest.raises(UnauthorizedError):
            require_api_key(_FakeRequest({"X-API-Key": "wrong"}))  # type: ignore[arg-type]

    def test_rejects_missing_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATELIER_API_KEY", "correct-horse-battery")
        get_settings.cache_clear()

        with pytest.raises(UnauthorizedError):
            require_api_key(_FakeRequest())  # type: ignore[arg-type]

    def test_rejects_prefix_of_correct_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Length mismatch must fail before any character comparison."""
        monkeypatch.setenv("ATELIER_API_KEY", "correct-horse-battery")
        get_settings.cache_clear()

        with pytest.raises(UnauthorizedError):
            require_api_key(_FakeRequest({"X-API-Key": "correct"}))  # type: ignore[arg-type]


class TestTokenBucketLimiter:
    def test_allows_up_to_the_limit(self) -> None:
        limiter = TokenBucketLimiter(limit_per_minute=3)
        for _ in range(3):
            limiter.check("10.0.0.1")

    def test_blocks_past_the_limit(self) -> None:
        limiter = TokenBucketLimiter(limit_per_minute=2)
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.1")

        with pytest.raises(RateLimitedError):
            limiter.check("10.0.0.1")

    def test_clients_are_counted_separately(self) -> None:
        limiter = TokenBucketLimiter(limit_per_minute=1)
        limiter.check("10.0.0.1")
        limiter.check("10.0.0.2")

    def test_reset_clears_state(self) -> None:
        limiter = TokenBucketLimiter(limit_per_minute=1)
        limiter.check("10.0.0.1")
        limiter.reset()
        limiter.check("10.0.0.1")

    def test_old_hits_fall_out_of_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        limiter = TokenBucketLimiter(limit_per_minute=1)
        clock = iter([0.0, 120.0])  # one reading per check() call
        monkeypatch.setattr("backend.app.api.deps.time.monotonic", lambda: next(clock))

        limiter.check("10.0.0.1")
        limiter.check("10.0.0.1")  # two minutes later, window has rolled over
