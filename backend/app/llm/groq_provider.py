"""Groq adapter. Default provider: free tier, OpenAI-compatible, fast."""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import ConfigurationError
from backend.app.llm.base import LLMProvider, Message


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str, vision_model: str, **kwargs: Any) -> None:
        if not api_key:
            raise ConfigurationError(
                "ATELIER_GROQ_API_KEY is not set.",
                public_message="No Groq API key is configured. Set ATELIER_GROQ_API_KEY.",
            )
        super().__init__(model, vision_model, **kwargs)

        from groq import Groq

        self._client = Groq(api_key=api_key)

    def _chat(
        self, messages: list[Message], *, temperature: float, max_tokens: int, json_mode: bool
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[m.model_dump() for m in messages],  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            # Groq requires the literal word "json" somewhere in the prompt for
            # this mode; the schema instruction in base.py always supplies it.
            response_format={"type": "json_object"} if json_mode else None,  # type: ignore[arg-type]
        )
        return response.choices[0].message.content or ""

    def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
        response = self._client.chat.completions.create(
            model=self.vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                    ],
                }
            ],
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content or ""
