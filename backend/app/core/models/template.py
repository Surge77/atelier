"""Template profiles: everything the renderers need to match an upload's style.

A profile is deliberately a description of the *template*, not a copy of its
content. The renderer still opens the original file to inherit theme, masters
and headers; the profile tells it which style names and layout indices to use
and what the writing should sound like.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Roles a generated slide can ask for. Mapped to real layout indices per
# template by the PPTX analyser.
LayoutRole = Literal[
    "title",
    "section_header",
    "title_content",
    "two_content",
    "title_only",
    "blank",
]


class ToneProfile(BaseModel):
    """How the uploaded document *sounds*, so new content can match it."""

    formality: str = "neutral professional"
    person: str = "third person"
    heading_case: str = "Title Case"
    bullet_style: str = "short noun phrases"
    avg_sentence_words: float = 18.0
    avg_paragraph_sentences: float = 3.0
    vocabulary_notes: list[str] = Field(default_factory=list)
    # Verbatim excerpts used as few-shot anchors at generation time. Far more
    # effective than an adjective like "formal".
    sample_excerpts: list[str] = Field(default_factory=list)

    def as_prompt_block(self) -> str:
        lines = [
            f"Formality: {self.formality}",
            f"Voice: {self.person}",
            f"Heading case: {self.heading_case}",
            f"Bullet style: {self.bullet_style}",
            f"Typical sentence length: about {self.avg_sentence_words:.0f} words",
            f"Typical paragraph: about {self.avg_paragraph_sentences:.0f} sentences",
        ]
        if self.vocabulary_notes:
            lines.append("Vocabulary: " + "; ".join(self.vocabulary_notes))
        if self.sample_excerpts:
            excerpts = "\n".join(f"  - {e}" for e in self.sample_excerpts[:4])
            lines.append(f"Excerpts from the source, match this cadence:\n{excerpts}")
        return "\n".join(lines)


class DocxTemplateProfile(BaseModel):
    """Style names that actually exist in an uploaded DOCX.

    Style names are read from the file rather than assumed, because a corporate
    template often renames or removes the built-ins. Applying a style name that
    is not present raises in python-docx, which would take the render down.
    """

    kind: Literal["docx"] = "docx"
    source_filename: str
    # Heading level (1-4) to the style name to apply for it.
    heading_styles: dict[int, str] = Field(default_factory=dict)
    title_style: str | None = None
    body_style: str = "Normal"
    bullet_style: str = "List Bullet"
    numbered_style: str | None = None
    quote_style: str | None = None
    table_style: str | None = None
    default_font: str | None = None
    default_font_size_pt: float | None = None
    has_header: bool = False
    has_footer: bool = False
    # The template's own outline, used to mirror its section structure.
    outline: list[str] = Field(default_factory=list)
    tone: ToneProfile = Field(default_factory=ToneProfile)

    def heading_style_for(self, level: int) -> str:
        """Nearest available heading style, falling back to body."""
        for candidate in (level, level - 1, level + 1, 1):
            if candidate in self.heading_styles:
                return self.heading_styles[candidate]
        return self.body_style


class SlideLayoutInfo(BaseModel):
    index: int
    name: str
    has_title: bool = False
    # Placeholder index of the main body, when the layout has one.
    body_placeholder_idx: int | None = None
    # Additional body placeholders, for two-content layouts.
    extra_body_placeholder_idxs: list[int] = Field(default_factory=list)
    placeholder_types: list[str] = Field(default_factory=list)


class PptxTemplateProfile(BaseModel):
    """Layouts, theme and rhythm of an uploaded PPTX."""

    kind: Literal["pptx"] = "pptx"
    source_filename: str
    slide_width_emu: int = 12192000
    slide_height_emu: int = 6858000
    aspect_ratio: str = "16:9"
    layouts: list[SlideLayoutInfo] = Field(default_factory=list)
    # Generation asks for a role; this resolves it to a real layout index.
    layout_map: dict[str, int] = Field(default_factory=dict)
    theme_major_font: str | None = None
    theme_minor_font: str | None = None
    theme_colors: dict[str, str] = Field(default_factory=dict)
    # Observed rhythm of the template's own slides, used as generation limits.
    avg_bullets_per_slide: float = 5.0
    max_bullets_per_slide: int = 6
    avg_words_per_bullet: float = 10.0
    slide_titles: list[str] = Field(default_factory=list)
    tone: ToneProfile = Field(default_factory=ToneProfile)

    def layout_index_for(self, role: str) -> int:
        """Resolve a role to a layout index, degrading through sensible kin."""
        fallbacks: dict[str, tuple[str, ...]] = {
            "title": ("title", "title_only", "title_content"),
            "section_header": ("section_header", "title_only", "title", "title_content"),
            "title_content": ("title_content", "title_only", "blank"),
            "two_content": ("two_content", "title_content", "blank"),
            "title_only": ("title_only", "title_content", "blank"),
            "blank": ("blank", "title_content"),
        }
        for candidate in fallbacks.get(role, (role, "title_content")):
            if candidate in self.layout_map:
                return self.layout_map[candidate]
        return 0


TemplateProfile = DocxTemplateProfile | PptxTemplateProfile
