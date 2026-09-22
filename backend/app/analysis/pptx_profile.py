"""Builds a ``PptxTemplateProfile`` from an uploaded presentation.

Layouts are classified structurally — by what placeholders they carry — with
the layout name used only as a tiebreaker. Structure is the reliable signal:
a template localised into another language, or one whose layouts were renamed
in-house, still has a title placeholder and two body placeholders where a
two-content layout should be.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any
from xml.etree import ElementTree

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from backend.app.core.logging import get_logger
from backend.app.core.models.template import PptxTemplateProfile, SlideLayoutInfo, ToneProfile
from backend.app.ingestion.base import ExtractedDocument

logger = get_logger(__name__)

MAX_SLIDE_TITLES = 40
DEFAULT_MAX_BULLETS = 6

# Placeholder types that hold slide body content.
_BODY_PLACEHOLDERS = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.SUBTITLE}
_TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}

# Name fragments that pin a role structure alone cannot distinguish.
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("title slide", "title"),
    ("section header", "section_header"),
    ("section", "section_header"),
    ("two content", "two_content"),
    ("comparison", "two_content"),
    ("title only", "title_only"),
    ("blank", "blank"),
    ("title and content", "title_content"),
)


def build_pptx_profile(
    path: Path,
    extracted: ExtractedDocument,
    tone: ToneProfile | None = None,
) -> PptxTemplateProfile:
    presentation = Presentation(str(path))
    layouts = [_describe_layout(index, layout) for index, layout in
               enumerate(presentation.slide_layouts)]

    bullets_per_slide, words_per_bullet = _rhythm(extracted)

    return PptxTemplateProfile(
        source_filename=extracted.source_filename,
        slide_width_emu=int(presentation.slide_width or 12192000),
        slide_height_emu=int(presentation.slide_height or 6858000),
        aspect_ratio=_aspect_ratio(presentation),
        layouts=layouts,
        layout_map=_build_layout_map(layouts, presentation),
        theme_major_font=_theme_font(presentation, "major"),
        theme_minor_font=_theme_font(presentation, "minor"),
        avg_bullets_per_slide=bullets_per_slide,
        max_bullets_per_slide=max(DEFAULT_MAX_BULLETS, round(bullets_per_slide) + 1),
        avg_words_per_bullet=words_per_bullet,
        slide_titles=extracted.outline()[:MAX_SLIDE_TITLES],
        tone=tone or ToneProfile(),
    )


def _describe_layout(index: int, layout: Any) -> SlideLayoutInfo:
    has_title = False
    body_idxs: list[int] = []
    types: list[str] = []

    for placeholder in layout.placeholders:
        placeholder_type = placeholder.placeholder_format.type
        types.append(str(placeholder_type))

        if placeholder_type in _TITLE_PLACEHOLDERS:
            has_title = True
        elif placeholder_type in _BODY_PLACEHOLDERS:
            body_idxs.append(placeholder.placeholder_format.idx)

    return SlideLayoutInfo(
        index=index,
        name=layout.name or f"Layout {index}",
        has_title=has_title,
        body_placeholder_idx=body_idxs[0] if body_idxs else None,
        extra_body_placeholder_idxs=body_idxs[1:],
        placeholder_types=types,
    )


def _structural_role(layout: SlideLayoutInfo) -> str:
    body_count = (1 if layout.body_placeholder_idx is not None else 0) + len(
        layout.extra_body_placeholder_idxs
    )

    if not layout.has_title and body_count == 0:
        return "blank"
    if layout.has_title and body_count == 0:
        return "title_only"
    if body_count >= 2:
        return "two_content"
    return "title_content"


def _build_layout_map(layouts: list[SlideLayoutInfo], presentation: Any) -> dict[str, int]:
    """Assign each role the best-matching layout index.

    A name hint wins over structure, and the first match for a role is kept so
    that a template with several similar layouts uses the earliest — which is
    the one a deck author would reach for.
    """
    mapping: dict[str, int] = {}

    for layout in layouts:
        role = _name_role(layout.name) or _structural_role(layout)
        mapping.setdefault(role, layout.index)

    # The first layout of a real template is its title slide, even when it is
    # named something in-house that no hint matches.
    if "title" not in mapping and layouts:
        mapping["title"] = layouts[0].index

    _log_unmapped(mapping, presentation)
    return mapping


def _name_role(name: str) -> str | None:
    lowered = name.lower()
    return next((role for fragment, role in _NAME_HINTS if fragment in lowered), None)


def _log_unmapped(mapping: dict[str, int], presentation: Any) -> None:
    missing = {"title", "title_content", "section_header"} - set(mapping)
    if missing:
        logger.info(
            "Template %s has no layout for %s; the renderer will fall back.",
            getattr(presentation, "name", "upload"),
            ", ".join(sorted(missing)),
        )


def _aspect_ratio(presentation: Any) -> str:
    width = presentation.slide_width or 0
    height = presentation.slide_height or 1
    ratio = width / height
    return "16:9" if ratio > 1.5 else "4:3"


def _theme_font(presentation: Any, which: str) -> str | None:
    """Read the theme's major (heading) or minor (body) latin typeface.

    The theme lives in its own package part, so this reaches through the
    master's relationships rather than using a python-pptx accessor, which
    does not expose it.
    """
    try:
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT

        theme_part = presentation.slide_master.part.part_related_by(RT.THEME)
        root = ElementTree.fromstring(theme_part.blob)

        namespace = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        element = root.find(f".//a:{which}Font/a:latin", namespace)
        return element.get("typeface") if element is not None else None
    except Exception as exc:
        logger.debug("Could not read the %s theme font: %s", which, exc)
        return None


def _rhythm(extracted: ExtractedDocument) -> tuple[float, float]:
    """How many bullets a slide carries and how long they run, on average."""
    bullets = extracted.blocks_of("bullet")
    if not bullets:
        return 5.0, 10.0

    per_slide: dict[int, int] = {}
    for block in bullets:
        per_slide[block.location] = per_slide.get(block.location, 0) + 1

    return (
        round(mean(per_slide.values()), 1),
        round(mean(len(block.text.split()) for block in bullets), 1),
    )
