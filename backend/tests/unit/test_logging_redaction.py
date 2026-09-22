"""Secrets must never survive a trip through the logger."""

from __future__ import annotations

import logging

from backend.app.config import Settings
from backend.app.core.logging import MASK, RedactingFilter, secrets_from_settings


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


class TestKeyShapes:
    def test_masks_openai_key(self) -> None:
        record = _record("calling with sk-abcdefghijklmnopqrstuvwxyz012345")
        RedactingFilter().filter(record)
        assert "sk-abcdefghijklmnop" not in record.msg
        assert MASK in record.msg

    def test_masks_groq_key(self) -> None:
        record = _record("key gsk_abcdefghijklmnopqrstuvwxyz0123 used")
        RedactingFilter().filter(record)
        assert "gsk_abcdefghij" not in record.msg

    def test_masks_anthropic_key(self) -> None:
        record = _record("sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        RedactingFilter().filter(record)
        assert "abcdefghij" not in record.msg

    def test_masks_pinecone_and_tavily_keys(self) -> None:
        record = _record("pcsk_abcdefghijklmnopqrst and tvly-abcdefghijklmnopqrst")
        RedactingFilter().filter(record)
        assert "abcdefghij" not in record.msg


class TestAssignments:
    def test_masks_api_key_assignment(self) -> None:
        record = _record("request params: api_key=supersecretvalue123")
        RedactingFilter().filter(record)
        assert "supersecretvalue123" not in record.msg

    def test_masks_json_token_field(self) -> None:
        record = _record('{"access_token": "abcd1234efgh5678"}')
        RedactingFilter().filter(record)
        assert "abcd1234efgh5678" not in record.msg

    def test_masks_bearer_header(self) -> None:
        record = _record("Authorization: Bearer aVeryLongOpaqueToken")
        RedactingFilter().filter(record)
        assert "aVeryLongOpaqueToken" not in record.msg


class TestLiteralSecrets:
    def test_masks_configured_secret_anywhere(self) -> None:
        secret = "an-unusual-configured-value"
        record = _record(f"upstream rejected {secret} for tenant 7")
        RedactingFilter({secret}).filter(record)
        assert secret not in record.msg
        assert "tenant 7" in record.msg

    def test_ignores_short_values_to_avoid_over_masking(self) -> None:
        """A 3-character secret would otherwise redact ordinary prose."""
        record = _record("the cat sat on the mat")
        RedactingFilter({"cat"}).filter(record)
        assert record.msg == "the cat sat on the mat"


class TestInterpolation:
    def test_redacts_after_argument_interpolation(self) -> None:
        record = _record("token for %s is %s", "alice", "gsk_abcdefghijklmnopqrstuvwx")
        RedactingFilter().filter(record)
        assert "gsk_abcdefghij" not in record.getMessage()
        assert "alice" in record.getMessage()


class TestSecretsFromSettings:
    def test_collects_only_non_empty_values(self) -> None:
        settings = Settings(groq_api_key="gsk_live_value", openai_api_key="")
        assert secrets_from_settings(settings) == {"gsk_live_value"}
