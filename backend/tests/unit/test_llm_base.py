"""Provider-agnostic LLM behaviour: JSON extraction, schema repair, retries.

No network. A scripted fake provider returns canned replies so the shared
logic in ``llm/base.py`` can be tested on its own.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.app.core.errors import ProviderError
from backend.app.llm.base import LLMProvider, Message, extract_json_object


class Recipe(BaseModel):
    title: str
    servings: int


class ScriptedProvider(LLMProvider):
    """Returns the next scripted reply, or raises the next scripted error."""

    name = "scripted"

    def __init__(self, replies: list[str | Exception], **kwargs: object) -> None:
        super().__init__("test-model", "test-vision", **kwargs)  # type: ignore[arg-type]
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    def _chat(self, messages, *, temperature, max_tokens, json_mode) -> str:  # type: ignore[no-untyped-def]
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _vision(self, prompt: str, image_b64: str, media_type: str) -> str:
        return self._chat([Message(role="user", content=prompt)],
                          temperature=0.0, max_tokens=100, json_mode=False)


class TestExtractJsonObject:
    def test_reads_a_bare_object(self) -> None:
        assert extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_strips_code_fences(self) -> None:
        assert extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_leading_prose(self) -> None:
        assert extract_json_object('Sure, here it is: {"a": 1}') == '{"a": 1}'

    def test_handles_nested_objects(self) -> None:
        raw = '{"outer": {"inner": {"deep": 1}}}'
        assert extract_json_object(f"text {raw} trailing") == raw

    def test_ignores_braces_inside_strings(self) -> None:
        """A brace in a string value must not close the object early."""
        raw = '{"note": "use { and } carefully", "n": 2}'
        assert extract_json_object(raw) == raw

    def test_ignores_escaped_quotes(self) -> None:
        raw = '{"note": "he said \\"hi\\" loudly"}'
        assert extract_json_object(raw) == raw

    def test_raises_when_no_object_present(self) -> None:
        with pytest.raises(ValueError, match="No JSON object"):
            extract_json_object("there is no json here")

    def test_raises_on_unterminated_object(self) -> None:
        with pytest.raises(ValueError, match="unterminated"):
            extract_json_object('{"a": 1')


class TestCompleteStructured:
    def test_parses_a_valid_reply(self) -> None:
        provider = ScriptedProvider(['{"title": "Soup", "servings": 4}'])

        result = provider.complete_structured(
            [Message(role="user", content="a recipe")], Recipe
        )

        assert result == Recipe(title="Soup", servings=4)

    def test_repairs_a_malformed_reply(self) -> None:
        provider = ScriptedProvider(
            ["not json at all", '{"title": "Stew", "servings": 2}']
        )

        result = provider.complete_structured([Message(role="user", content="x")], Recipe)

        assert result.title == "Stew"
        assert len(provider.calls) == 2

    def test_feeds_the_validation_error_back_to_the_model(self) -> None:
        """The repair turn must say what was wrong, not just ask again."""
        provider = ScriptedProvider(
            ['{"title": "Pie"}', '{"title": "Pie", "servings": 6}']
        )

        provider.complete_structured([Message(role="user", content="x")], Recipe)

        repair_turn = provider.calls[1][-1].content
        assert "servings" in repair_turn

    def test_gives_up_after_the_repair_budget(self) -> None:
        provider = ScriptedProvider(["nope", "still nope", "nope again"])

        with pytest.raises(ProviderError, match="Recipe"):
            provider.complete_structured([Message(role="user", content="x")], Recipe)

    def test_repairs_a_provider_side_json_rejection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some providers validate JSON themselves and 400 on bad output.

        That is a bad generation, not a bad request, so it must be repaired
        rather than escape as a provider error.
        """
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        rejection = RuntimeError(
            "Error code: 400 - {'error': {'code': 'json_validate_failed', "
            "'failed_generation': '{\"title\": \"Pie\", \"servings\":'}}"
        )
        provider = ScriptedProvider([rejection, '{"title": "Pie", "servings": 6}'])

        result = provider.complete_structured([Message(role="user", content="x")], Recipe)

        assert result.servings == 6

    def test_shows_the_model_its_own_broken_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        rejection = RuntimeError(
            "Error code: 400 - {'error': {'code': 'json_validate_failed', "
            "'failed_generation': '{\"title\": \"Broken\", \"servings\":'}}"
        )
        provider = ScriptedProvider([rejection, '{"title": "T", "servings": 1}'])

        provider.complete_structured([Message(role="user", content="x")], Recipe)

        repair_turn = "".join(m.content for m in provider.calls[1])
        assert "Broken" in repair_turn

    def test_a_non_json_provider_error_still_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An auth failure must not be retried three times as a bad generation."""
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        provider = ScriptedProvider([ValueError("401 invalid api key")])

        with pytest.raises(ProviderError, match="401"):
            provider.complete_structured([Message(role="user", content="x")], Recipe)

        assert len(provider.calls) == 1

    def test_appends_the_schema_instruction(self) -> None:
        provider = ScriptedProvider(['{"title": "T", "servings": 1}'])

        provider.complete_structured([Message(role="user", content="x")], Recipe)

        assert "JSON Schema" in provider.calls[0][-1].content


class TestRetries:
    def test_retries_a_rate_limit_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        provider = ScriptedProvider([RuntimeError("rate limit exceeded"), "ok"], max_retries=3)

        assert provider.complete([Message(role="user", content="x")]) == "ok"

    def test_does_not_retry_a_client_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        provider = ScriptedProvider([ValueError("invalid model name"), "ok"], max_retries=3)

        with pytest.raises(ProviderError):
            provider.complete([Message(role="user", content="x")])

        assert len(provider.calls) == 1

    def test_retries_a_status_coded_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)

        overloaded = RuntimeError("service issue")
        overloaded.status_code = 503  # type: ignore[attr-defined]
        provider = ScriptedProvider([overloaded, "recovered"], max_retries=2)

        assert provider.complete([Message(role="user", content="x")]) == "recovered"

    def test_raises_after_exhausting_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.app.llm.base.time.sleep", lambda _: None)
        provider = ScriptedProvider(
            [RuntimeError("timeout"), RuntimeError("timeout")], max_retries=2
        )

        with pytest.raises(ProviderError, match="failed"):
            provider.complete([Message(role="user", content="x")])
