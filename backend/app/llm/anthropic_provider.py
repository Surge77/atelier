"""Anthropic adapter.

The Messages API takes the system prompt as a separate argument rather than as
a message, so system turns are pulled out and concatenated here.
"""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import ConfigurationError
from backend.app.llm.base import LLMProvider, Message


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, vision_model: str, **kwargs: Any) -> None:
        if not api_key:
            raise ConfigurationError(
                "ATELIER_ANTHROPIC_API_KEY is not set.",
                public_message=(
                    "No Anthropic API key is configured. Set ATELIER_ANTHROPIC_API_KEY. "
                    "Note that a Claude subscription is not an API key."
                ),
            )
        super().__init__(model, vision_model, **kwargs)

        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)

    def _chat(
        self, messages: list[Message], *, temperature: float, max_tokens: int, json_mode: bool
    ) -> str:
        # Anthropic has no JSON response mode, so json_mode is not forwarded.
        # The schema instruction added in base.py carries that requirement.
        del json_mode
        system_prompt, turns = _split_system(messages)
        response = self._client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=[{"role": m.role, "content": m.content} for m in turns],  # type: ignore[misc]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _first_text(response)

    def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
        content: list[Any] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": image_b64},
            },
            {"type": "text", "text": prompt},
        ]
        response = self._client.messages.create(
            model=self.vision_model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        return _first_text(response)


def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns = [m for m in messages if m.role != "system"]
    # The API rejects a conversation that does not start with a user turn.
    if not turns:
        turns = [Message(role="user", content="Proceed.")]
    return system, turns


def _first_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""
