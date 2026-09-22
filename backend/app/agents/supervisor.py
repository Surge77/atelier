"""Agent 1: the supervisor.

Makes one structured call that decides what this turn needs — which specialists
to invoke, what to produce, and what each producer should be told. The rest of
the turn is deterministic sequencing driven by that decision.

That is deliberate. Letting a model re-decide after every step is where
multi-agent systems become slow, expensive and impossible to explain; the
routing here is a single auditable object that the UI displays and the
orchestrator follows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.errors import ProviderError
from backend.app.core.logging import get_logger
from backend.app.llm.base import LLMProvider, Message

logger = get_logger(__name__)

SUPERVISOR_AGENT = "supervisor"

Intent = Literal["create", "edit", "convert", "answer"]

DEFAULT_SLIDE_COUNT = 10
MAX_SLIDE_COUNT = 40

SYSTEM_PROMPT = """\
You are the orchestrator of a document generation system. Read the user's
request and decide what this turn requires.

Guidance:
- intent "create" produces new artifacts; "edit" changes an existing one;
  "convert" turns a document into a deck or the reverse; "answer" is a question
  that needs no artifact.
- Request web research only when the answer depends on current, external
  information the user does not already have.
- Request enterprise retrieval when the request refers to internal, company or
  policy knowledge.
- produce_document and produce_deck are independent; a request can need both.
- The briefs you write are the instructions the writing agents receive. Make
  them specific and self-contained; they do not see this conversation.
- If the user names a slide count, use it.
- For an edit, set target_artifact_id to the id of the artifact the user
  means. An instruction about slides means the deck; one about sections or
  the report means the document.
"""


class ExecutionPlan(BaseModel):
    """The supervisor's routing decision for one turn."""

    intent: Intent = "create"
    reasoning: str = Field(default="", description="One sentence on why this routing")

    needs_web_research: bool = False
    needs_enterprise_rag: bool = False

    produce_document: bool = False
    produce_deck: bool = False

    document_brief: str = Field(
        default="", description="Self-contained instructions for the document writer"
    )
    deck_brief: str = Field(
        default="", description="Self-contained instructions for the deck builder"
    )
    slide_count: int = Field(default=DEFAULT_SLIDE_COUNT, ge=1, le=MAX_SLIDE_COUNT)

    edit_instruction: str = Field(
        default="", description="For intent 'edit': what to change, in the user's terms"
    )
    target_artifact_id: str = Field(
        default="", description="For 'edit' or 'convert': which artifact, if named"
    )
    reply: str = Field(
        default="", description="For intent 'answer': the reply to the user"
    )

    def produces_anything(self) -> bool:
        return self.produce_document or self.produce_deck

    def agents_involved(self) -> list[str]:
        """The specialists this plan will invoke, for the trace."""
        agents = ["supervisor"]
        if self.needs_web_research:
            agents.append("web_research")
        if self.needs_enterprise_rag:
            agents.append("rag")
        if self.produce_document:
            agents.append("document_generation")
        if self.produce_deck:
            agents.append("ppt_generation")
        if self.intent == "edit":
            agents.append("editing")
        if self.produces_anything():
            agents.append("validation")
        return agents


def plan_turn(
    user_message: str,
    provider: LLMProvider,
    context: str = "",
) -> ExecutionPlan:
    """Decide how to handle this turn.

    ``context`` describes what already exists — uploaded templates and previous
    artifacts — so the supervisor can tell "make it shorter" (an edit) from
    "write a summary" (a creation).
    """
    try:
        plan = provider.complete_structured(
            [
                Message(role="system", content=SYSTEM_PROMPT),
                Message(
                    role="user",
                    content=f"{context}\n\nUSER REQUEST\n{user_message}".strip(),
                ),
            ],
            ExecutionPlan,
            temperature=0.0,
        )
    except ProviderError as exc:
        logger.warning("Supervisor routing failed, falling back: %s", exc)
        return _fallback_plan(user_message)

    logger.info(
        "Routing: intent=%s web=%s rag=%s doc=%s deck=%s",
        plan.intent,
        plan.needs_web_research,
        plan.needs_enterprise_rag,
        plan.produce_document,
        plan.produce_deck,
    )
    return plan


def _fallback_plan(user_message: str) -> ExecutionPlan:
    """Keyword routing for when the model is unreachable.

    Crude, but it keeps the system answering rather than erroring out, and it
    is only reached after the provider's own retries are exhausted.
    """
    lowered = user_message.lower()
    wants_deck = any(word in lowered for word in ("slide", "deck", "presentation", "ppt"))
    wants_document = any(
        word in lowered for word in ("document", "proposal", "report", "doc", "write-up")
    )

    return ExecutionPlan(
        intent="create",
        reasoning="Model routing was unavailable; matched on keywords.",
        needs_web_research=any(
            word in lowered for word in ("research", "latest", "current", "trends", "news")
        ),
        produce_document=wants_document or not wants_deck,
        produce_deck=wants_deck,
        document_brief=user_message,
        deck_brief=user_message,
    )


def describe_context(
    template_names: list[str], artifact_titles: list[str]
) -> str:
    """The "what already exists" block the supervisor reads."""
    parts: list[str] = []

    if template_names:
        parts.append("UPLOADED TEMPLATES\n" + "\n".join(f"- {n}" for n in template_names))
    if artifact_titles:
        parts.append(
            "ARTIFACTS ALREADY GENERATED IN THIS CONVERSATION\n"
            + "\n".join(f"- {t}" for t in artifact_titles)
            + "\nWhen editing one, put its leading id in target_artifact_id."
        )

    return "\n\n".join(parts)
