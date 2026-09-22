"""Agent 8: validation.

Checks a generated plan before it is rendered and shown to the user. Almost
every check is deterministic — citation resolution, structure, bullet budgets,
empty content — because those are the failures that actually happen and they
need no model call to find.

Severity decides what happens next. An ``error`` triggers one bounded repair
pass through generation; a ``warning`` is reported but does not block. The
budget is one repair, deliberately: a model that failed the same structural
check twice will usually fail it a third time, and the user is waiting.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.logging import get_logger
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import ContentPlan, DeckPlan, DocumentPlan
from backend.app.core.models.template import PptxTemplateProfile

logger = get_logger(__name__)

VALIDATION_AGENT = "validation"

Severity = Literal["error", "warning"]

MIN_SECTIONS = 2
MIN_WORDS_PER_SECTION = 25
MAX_WORDS_PER_BULLET = 25
# Below this share of substantive blocks carrying a source, a researched
# document is not really grounded in what was retrieved.
MIN_CITATION_COVERAGE = 0.3


class ValidationIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    location: str = ""


class ValidationReport(BaseModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    citation_coverage: float = 0.0
    word_count: int = 0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def summary(self) -> str:
        if self.passed and not self.issues:
            return "Validation passed."
        errors = len(self.errors)
        warnings = len(self.issues) - errors
        return f"Validation found {errors} error(s) and {warnings} warning(s)."

    def repair_instruction(self) -> str:
        """What to tell the generator so the next attempt is different."""
        lines = [f"- {issue.message}" for issue in self.errors]
        return "The previous draft had these problems. Fix them:\n" + "\n".join(lines)


def validate_plan(
    plan: ContentPlan,
    citations: list[Citation],
    expected_slide_count: int | None = None,
    deck_profile: PptxTemplateProfile | None = None,
    had_sources: bool = False,
) -> ValidationReport:
    """Check a plan and decide whether it can be rendered."""
    known_ids = {citation.id for citation in citations}

    if isinstance(plan, DeckPlan):
        issues = _validate_deck(plan, known_ids, expected_slide_count, deck_profile)
        coverage = _deck_coverage(plan)
        words = sum(len(" ".join(s.bullets).split()) for s in plan.slides)
    else:
        issues = _validate_document(plan, known_ids)
        coverage = _document_coverage(plan)
        words = plan.word_count()

    if had_sources and coverage < MIN_CITATION_COVERAGE:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="LOW_CITATION_COVERAGE",
                message=(
                    f"Only {coverage:.0%} of the content cites a source, "
                    "though research was performed."
                ),
            )
        )

    report = ValidationReport(
        passed=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        citation_coverage=round(coverage, 3),
        word_count=words,
    )
    logger.info("%s coverage=%.0f%%", report.summary(), coverage * 100)
    return report


def _validate_document(plan: DocumentPlan, known_ids: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not plan.title.strip():
        issues.append(
            ValidationIssue(severity="error", code="NO_TITLE", message="The document has no title.")
        )

    if len(plan.sections) < MIN_SECTIONS:
        issues.append(
            ValidationIssue(
                severity="error",
                code="TOO_FEW_SECTIONS",
                message=f"Only {len(plan.sections)} section(s); a document needs at least "
                f"{MIN_SECTIONS}.",
            )
        )

    for section in plan.sections:
        where = f"section {section.id} ({section.heading!r})"

        if not section.blocks or all(block.is_empty() for block in section.blocks):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="EMPTY_SECTION",
                    message=f"Section {section.heading!r} has no content.",
                    location=where,
                )
            )
        elif section.word_count() < MIN_WORDS_PER_SECTION:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="THIN_SECTION",
                    message=f"Section {section.heading!r} is only "
                    f"{section.word_count()} words.",
                    location=where,
                )
            )

        issues.extend(_unresolved(section.citation_ids(), known_ids, where))

    return issues


def _validate_deck(
    plan: DeckPlan,
    known_ids: set[str],
    expected_slide_count: int | None,
    profile: PptxTemplateProfile | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if expected_slide_count and len(plan.slides) != expected_slide_count:
        issues.append(
            ValidationIssue(
                severity="error",
                code="SLIDE_COUNT_MISMATCH",
                message=f"{len(plan.slides)} slides produced, "
                f"{expected_slide_count} were requested.",
            )
        )

    budget = profile.max_bullets_per_slide if profile else 6

    for slide in plan.slides:
        where = f"slide {slide.id} ({slide.title!r})"

        if not slide.title.strip():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="SLIDE_NO_TITLE",
                    message=f"Slide {slide.id} has no title.",
                    location=where,
                )
            )

        if len(slide.bullets) > budget:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="TOO_MANY_BULLETS",
                    message=f"Slide {slide.title!r} has {len(slide.bullets)} bullets, "
                    f"above the template's {budget}.",
                    location=where,
                )
            )

        for bullet in slide.bullets:
            if len(bullet.split()) > MAX_WORDS_PER_BULLET:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="LONG_BULLET",
                        message=f"A bullet on {slide.title!r} runs to "
                        f"{len(bullet.split())} words.",
                        location=where,
                    )
                )
                break  # one report per slide is enough

        issues.extend(_unresolved(slide.citation_ids, known_ids, where))

    return issues


def _unresolved(
    citation_ids: list[str], known_ids: set[str], where: str
) -> list[ValidationIssue]:
    """An id that resolves to nothing would render as a fabricated source."""
    missing = sorted({cid for cid in citation_ids if cid not in known_ids})
    return [
        ValidationIssue(
            severity="error",
            code="UNRESOLVED_CITATION",
            message=f"Citation {cid} does not match any retrieved source.",
            location=where,
        )
        for cid in missing
    ]


def _document_coverage(plan: DocumentPlan) -> float:
    blocks = [
        block
        for section in plan.sections
        for block in section.blocks
        if not block.is_empty()
    ]
    if not blocks:
        return 0.0
    return sum(1 for block in blocks if block.citation_ids) / len(blocks)


def _deck_coverage(plan: DeckPlan) -> float:
    # Title and divider slides make no claims, so counting them would
    # understate how well the substantive slides are sourced.
    content = [slide for slide in plan.slides if slide.bullets]
    if not content:
        return 0.0
    return sum(1 for slide in content if slide.citation_ids) / len(content)
