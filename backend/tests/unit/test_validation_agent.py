"""Validation: the gate that makes the citation guarantee enforceable."""

from __future__ import annotations

from backend.app.agents.validation_agent import validate_plan
from backend.app.core.models.citation import Citation
from backend.app.core.models.content import Block, DeckPlan, DocumentPlan, Section, Slide
from backend.app.core.models.template import PptxTemplateProfile

CITATIONS = [
    Citation(id="S1", kind="web", title="A", url="https://a.example"),
    Citation(id="S2", kind="web", title="B", url="https://b.example"),
]


# Long enough to clear MIN_WORDS_PER_SECTION, so the thin-section warning
# does not fire in tests that are about something else.
BODY = "This sentence carries enough real words to count as substantive prose. " * 6


def _section(heading: str, text: str = BODY, citations: list[str] | None = None) -> Section:
    return Section(heading=heading, blocks=[Block(text=text, citation_ids=citations or [])])


def _document(*sections: Section, title: str = "Report") -> DocumentPlan:
    return DocumentPlan(title=title, sections=list(sections))


class TestDocumentStructure:
    def test_passes_a_well_formed_document(self) -> None:
        plan = _document(_section("One"), _section("Two"))

        assert validate_plan(plan, CITATIONS).passed

    def test_rejects_a_missing_title(self) -> None:
        plan = _document(_section("One"), _section("Two"), title="")

        report = validate_plan(plan, CITATIONS)

        assert not report.passed
        assert any(issue.code == "NO_TITLE" for issue in report.errors)

    def test_rejects_too_few_sections(self) -> None:
        report = validate_plan(_document(_section("Only")), CITATIONS)

        assert any(issue.code == "TOO_FEW_SECTIONS" for issue in report.errors)

    def test_rejects_an_empty_section(self) -> None:
        plan = _document(_section("One"), Section(heading="Hollow"))

        report = validate_plan(plan, CITATIONS)

        assert any(issue.code == "EMPTY_SECTION" for issue in report.errors)

    def test_warns_about_a_thin_section_without_blocking(self) -> None:
        plan = _document(_section("One"), _section("Two", text="Very short."))

        report = validate_plan(plan, CITATIONS)

        assert report.passed
        assert any(issue.code == "THIN_SECTION" for issue in report.issues)


class TestCitationResolution:
    def test_rejects_an_unresolvable_citation(self) -> None:
        """This is the check that stops a fabricated source reaching References."""
        plan = _document(_section("One", citations=["S9"]), _section("Two"))

        report = validate_plan(plan, CITATIONS)

        assert not report.passed
        assert any(issue.code == "UNRESOLVED_CITATION" for issue in report.errors)

    def test_accepts_known_citations(self) -> None:
        plan = _document(_section("One", citations=["S1"]), _section("Two", citations=["S2"]))

        assert validate_plan(plan, CITATIONS).passed

    def test_reports_each_unknown_id_once(self) -> None:
        plan = _document(_section("One", citations=["S9", "S9"]), _section("Two"))

        unresolved = [
            i for i in validate_plan(plan, CITATIONS).errors if i.code == "UNRESOLVED_CITATION"
        ]

        assert len(unresolved) == 1

    def test_measures_coverage(self) -> None:
        plan = _document(_section("One", citations=["S1"]), _section("Two"))

        assert validate_plan(plan, CITATIONS).citation_coverage == 0.5

    def test_warns_when_research_ran_but_nothing_is_cited(self) -> None:
        plan = _document(_section("One"), _section("Two"))

        report = validate_plan(plan, CITATIONS, had_sources=True)

        assert any(issue.code == "LOW_CITATION_COVERAGE" for issue in report.issues)

    def test_no_coverage_warning_when_no_research_ran(self) -> None:
        plan = _document(_section("One"), _section("Two"))

        report = validate_plan(plan, [], had_sources=False)

        assert report.issues == []


class TestDeckValidation:
    def _deck(self, *slides: Slide) -> DeckPlan:
        return DeckPlan(title="Deck", slides=list(slides))

    def test_passes_a_well_formed_deck(self) -> None:
        plan = self._deck(Slide(title="One", bullets=["a"]), Slide(title="Two", bullets=["b"]))

        assert validate_plan(plan, CITATIONS).passed

    def test_rejects_a_slide_count_mismatch(self) -> None:
        plan = self._deck(Slide(title="Only", bullets=["a"]))

        report = validate_plan(plan, CITATIONS, expected_slide_count=12)

        assert not report.passed
        assert any(issue.code == "SLIDE_COUNT_MISMATCH" for issue in report.errors)

    def test_rejects_a_slide_with_no_title(self) -> None:
        report = validate_plan(self._deck(Slide(title="", bullets=["a"])), CITATIONS)

        assert any(issue.code == "SLIDE_NO_TITLE" for issue in report.errors)

    def test_warns_when_a_slide_exceeds_the_bullet_budget(self) -> None:
        profile = PptxTemplateProfile(source_filename="t", max_bullets_per_slide=3)
        plan = self._deck(Slide(title="Busy", bullets=[f"point {i}" for i in range(6)]))

        report = validate_plan(plan, CITATIONS, deck_profile=profile)

        assert report.passed
        assert any(issue.code == "TOO_MANY_BULLETS" for issue in report.issues)

    def test_warns_about_a_paragraph_length_bullet(self) -> None:
        plan = self._deck(Slide(title="Wordy", bullets=["word " * 40]))

        report = validate_plan(plan, CITATIONS)

        assert any(issue.code == "LONG_BULLET" for issue in report.issues)

    def test_reports_a_long_bullet_once_per_slide(self) -> None:
        plan = self._deck(Slide(title="Wordy", bullets=["word " * 40, "word " * 40]))

        long_bullets = [i for i in validate_plan(plan, CITATIONS).issues if i.code == "LONG_BULLET"]

        assert len(long_bullets) == 1

    def test_coverage_ignores_title_and_divider_slides(self) -> None:
        """Counting them would understate how well the content is sourced."""
        plan = self._deck(
            Slide(title="Title", layout="title"),
            Slide(title="Content", bullets=["a"], citation_ids=["S1"]),
        )

        assert validate_plan(plan, CITATIONS).citation_coverage == 1.0


class TestReporting:
    def test_summary_counts_errors_and_warnings(self) -> None:
        plan = _document(_section("One", citations=["S9"]), _section("Two", text="Short."))

        summary = validate_plan(plan, CITATIONS).summary()

        assert "1 error(s)" in summary
        assert "1 warning(s)" in summary

    def test_passing_report_says_so(self) -> None:
        plan = _document(_section("One"), _section("Two"))

        assert validate_plan(plan, CITATIONS).summary() == "Validation passed."

    def test_repair_instruction_lists_only_the_errors(self) -> None:
        plan = _document(_section("One", citations=["S9"]), _section("Two", text="Short."))

        instruction = validate_plan(plan, CITATIONS).repair_instruction()

        assert "S9" in instruction
        assert "Short" not in instruction
