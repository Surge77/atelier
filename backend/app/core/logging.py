"""Logging setup with secret redaction.

Agent systems log prompts, URLs and request bodies constantly, so a key that
leaks into one log line ends up in every downstream sink. The filter here does
two passes: it masks anything shaped like a vendor API key, and it masks the
literal secret values this process was configured with.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

MASK = "***redacted***"

# Vendor key shapes: OpenAI (sk-), Groq (gsk_), Anthropic (sk-ant-),
# Pinecone (pcsk_), Tavily (tvly-).
_KEY_SHAPES = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{16,}"
    r"|pcsk_[A-Za-z0-9_-]{16,}|tvly-[A-Za-z0-9-]{16,})\b"
)

# `api_key=...`, `"access_token": "..."`, `Authorization: Bearer ...`
# A `:` or `=` is required so ordinary prose ("token for alice") is left alone.
# Group 3 preserves an auth scheme word so only the credential itself is masked.
_KEY_ASSIGNMENTS = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|authorization)"
    r"(\"?\s*[:=]\s*\"?)"
    r"((?:bearer|token)\s+)?"
    r"([^\s\"',}]+)"
)

# A bearer credential that appears without an `Authorization:` prefix.
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")


class RedactingFilter(logging.Filter):
    """Masks secrets in every record that passes through a handler."""

    def __init__(self, literal_secrets: set[str] | None = None) -> None:
        super().__init__()
        # Short values would match far too much ordinary text.
        self._literals = {s for s in (literal_secrets or set()) if len(s) >= 8}

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        # getMessage() already interpolated args; keeping them would double-format.
        record.args = ()
        return True

    def redact(self, text: str) -> str:
        for secret in self._literals:
            text = text.replace(secret, MASK)
        text = _KEY_SHAPES.sub(MASK, text)
        text = _KEY_ASSIGNMENTS.sub(rf"\1\2\3{MASK}", text)
        return _BEARER.sub(rf"\1{MASK}", text)


def configure_logging(level: str = "INFO", literal_secrets: set[str] | None = None) -> None:
    """Install a single stderr handler with redaction. Safe to call twice."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(RedactingFilter(literal_secrets))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # These are chatty at INFO and drown out our own agent trace.
    for noisy in ("httpx", "httpcore", "chromadb", "urllib3", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def secrets_from_settings(settings: Any) -> set[str]:
    """Collect the configured secret values so they can be masked literally."""
    names = (
        "groq_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "pinecone_api_key",
        "tavily_api_key",
        "api_key",
    )
    return {value for name in names if (value := getattr(settings, name, ""))}
