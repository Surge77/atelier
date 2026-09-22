"""LLM provider contract and the shared behaviour every adapter inherits.

Adapters implement two primitives only — ``_chat`` and ``_vision``. Everything
harder (retries, JSON extraction, schema validation, repair) lives here once,
so adding a provider means writing about thirty lines.

Structured output is done by asking for JSON and validating against a Pydantic
model rather than by using any single vendor's schema feature, because those
differ across the three providers we support and quietly change over time.
"""

from __future__ import annotations

import json
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.errors import ProviderError
from backend.app.core.logging import get_logger

logger = get_logger(__name__)

Role = Literal["system", "user", "assistant"]
ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_JSON_REPAIR_ATTEMPTS = 2


class Message(BaseModel):
    role: Role
    content: str


class LLMProvider(ABC):
    """What the agents are allowed to ask of a language model."""

    name: str

    def __init__(self, model: str, vision_model: str, *, temperature: float = 0.3,
                 max_tokens: int = 8000, max_retries: int = 3) -> None:
        self.model = model
        self.vision_model = vision_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    # --- Adapter surface -------------------------------------------------

    @abstractmethod
    def _chat(self, messages: list[Message], *, temperature: float, max_tokens: int,
              json_mode: bool) -> str:
        """One completion call. Raises the vendor's own exception on failure."""

    @abstractmethod
    def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
        """Describe or transcribe an image."""

    # --- Public API ------------------------------------------------------

    def complete(self, messages: list[Message], *, temperature: float | None = None,
                 max_tokens: int | None = None, json_mode: bool = False) -> str:
        return self._with_retries(
            lambda: self._chat(
                messages,
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=max_tokens or self.max_tokens,
                json_mode=json_mode,
            )
        )

    def describe_image(self, prompt: str, image_b64: str, media_type: str = "image/png") -> str:
        return self._with_retries(lambda: self._vision(prompt, image_b64, media_type))

    def complete_structured(self, messages: list[Message], schema: type[ModelT],
                            *, temperature: float | None = None) -> ModelT:
        """Return a validated instance of ``schema``.

        On a malformed or non-conforming reply the validation error is handed
        back to the model as a follow-up turn. That repairs far more failures
        than a blind retry, because the model is told exactly what was wrong.
        """
        conversation = [*messages, Message(role="system", content=_schema_instruction(schema))]

        last_error = ""
        for attempt in range(MAX_JSON_REPAIR_ATTEMPTS + 1):
            raw = ""
            try:
                raw = self.complete(conversation, temperature=temperature, json_mode=True)
                return schema.model_validate(json.loads(extract_json_object(raw)))
            except ProviderError as exc:
                # Some providers validate JSON server-side and reject malformed
                # output with a 400. That is a bad generation, not a bad
                # request, so it belongs in this loop rather than escaping it.
                if not _is_json_generation_failure(exc):
                    raise
                last_error = _failed_generation_of(exc)
                raw = last_error
                logger.warning(
                    "Provider rejected its own JSON on attempt %s/%s",
                    attempt + 1, MAX_JSON_REPAIR_ATTEMPTS + 1,
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    "Structured output attempt %s/%s failed: %s",
                    attempt + 1, MAX_JSON_REPAIR_ATTEMPTS + 1, last_error[:300],
                )

            conversation = [
                *conversation,
                Message(role="assistant", content=raw[:4000]),
                Message(
                    role="user",
                    content=(
                        f"That reply was not valid for the schema: {last_error[:800]}\n"
                        "Reply again with only the corrected JSON object. Every array "
                        "element must be a separate, complete object."
                    ),
                ),
            ]

        raise ProviderError(
            f"{self.name} could not produce valid {schema.__name__}: {last_error[:500]}",
            public_message="The assistant returned an unusable response. Try rephrasing.",
        )

    # --- Retry -----------------------------------------------------------

    def _with_retries(self, call: Any) -> str:
        """Exponential backoff with jitter for transient upstream failures."""
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                return call()
            except Exception as exc:
                if attempt == self.max_retries or not _is_transient(exc):
                    raise ProviderError(f"{self.name} call failed: {exc}") from exc
                sleep_for = delay + random.uniform(0, 0.5)
                logger.warning(
                    "%s transient failure (attempt %s/%s), retrying in %.1fs: %s",
                    self.name, attempt, self.max_retries, sleep_for, exc,
                )
                time.sleep(sleep_for)
                delay *= 2
        raise ProviderError(f"{self.name} exhausted retries.")


_JSON_FAILURE_MARKERS = ("json_validate_failed", "failed to generate json")


def _is_json_generation_failure(exc: Exception) -> bool:
    """True when the provider rejected the model's own JSON output."""
    text = str(exc).lower()
    return any(marker in text for marker in _JSON_FAILURE_MARKERS)


def _failed_generation_of(exc: Exception) -> str:
    """The malformed output, when the provider echoed it back.

    Showing the model its own broken JSON repairs far more reliably than
    telling it that "generation failed".
    """
    text = str(exc)
    marker = "'failed_generation':"
    start = text.find(marker)
    if start == -1:
        return text[:800]
    return text[start + len(marker) : start + len(marker) + 3000].strip()


def _is_transient(exc: Exception) -> bool:
    """Rate limits, overload and timeouts are worth retrying; 400s are not."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status in {408, 409, 429, 500, 502, 503, 504}

    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("rate limit", "timeout", "timed out", "overloaded",
                       "temporarily unavailable", "connection")
    )


def extract_json_object(text: str) -> str:
    """Pull the first balanced JSON object out of a model reply.

    Models wrap JSON in ``` fences or add a sentence of preamble often enough
    that parsing the raw string directly is unreliable.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in the reply.")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("JSON object in the reply is unterminated.")


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else - no prose, no code "
        "fences. It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=None)}"
    )
