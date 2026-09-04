"""Heading detection from typography.

Text patterns alone cannot tell a heading from a short sentence, and font size
alone cannot tell a heading from a pull quote. This module combines them: size
and weight relative to the body text propose the candidates, and shape (length,
sentence structure, section numbering, capitalisation) vetoes the false ones.

Levels come from ranking the distinct type styles used by the candidates, so a
document that sets its sections in 14pt bold and its subsections in 12pt bold
gets ``h1``/``h2`` regardless of what the absolute sizes are.
"""

from __future__ import annotations

from dataclasses import dataclass

from .._text import heading_number_depth, is_sentence_like, split_list_marker
from ..options import ParseOptions
from ..readers.base import RawLine
from ..types import StyleInfo

__all__ = [
    "HeadingModel",
    "body_size",
    "build_heading_model",
    "is_heading_candidate",
    "normalize_levels",
    "rank_levels",
]

#: Above this share of bold lines, weight carries no information.
_BOLD_SATURATION = 0.55
#: A line this much bigger than body text is a heading whatever its shape.
_OVERRIDE_RATIO = 1.45
#: Longest line still eligible on capitalisation alone.
_MAX_CAPS_CHARS = 90


#: Lines shorter than this are ignored when voting on the body text size.
_MIN_BODY_CHARS = 25


def body_size(lines: list[RawLine]) -> float:
    """The dominant body-text size, weighted by characters.

    Two filters make this survive documents that are mostly furniture. Only
    substantial lines vote, because headings, page numbers and captions are all
    short while body text is long; and weighting is by character count, so a
    document with many short headings and few long paragraphs still resolves to
    the paragraph size. Ties break towards the *smaller* size, since body text
    is never the largest type on a page.
    """
    pool = [ln for ln in lines if ln.char_count() >= _MIN_BODY_CHARS] or lines
    weights: dict[float, int] = {}
    for line in pool:
        key = round(line.dominant_size(), 1)
        weights[key] = weights.get(key, 0) + line.char_count()
    if not weights:
        return 10.0
    return max(weights.items(), key=lambda kv: (kv[1], -kv[0]))[0]


@dataclass(slots=True)
class HeadingModel:
    """Everything needed to classify a line, derived once per document."""

    body: float
    #: ``(size, bold) -> level``, built by ranking the candidate styles.
    levels: dict[tuple[float, bool], int]
    #: True when bold is so common in this document that it means nothing.
    bold_saturated: bool
    #: True when enough headings carry section numbers to trust them.
    trust_numbering: bool
    options: ParseOptions

    def level_for(self, line: RawLine) -> int | None:
        """The heading level of ``line``, or ``None`` if it is not a heading."""
        if not self.options.detect_headings:
            return None
        if not _is_candidate(line, self.body, self.bold_saturated, self.options):
            return None

        key = (round(line.dominant_size(), 1), line.bold_ratio() >= 0.6)
        level = self.levels.get(key)
        if level is None:
            return None

        if self.trust_numbering:
            depth = heading_number_depth(line.text)
            if depth is not None:
                # An explicit section number is the author stating the level
                # outright, which beats anything inferred from font metrics.
                return min(depth, self.options.max_heading_level)
        return level

    def style_for(self, line: RawLine) -> StyleInfo:
        """Line typography with ``size_ratio`` filled in against the body size."""
        base = line.style()
        ratio = base.size / self.body if self.body else 1.0
        return StyleInfo(
            size=base.size,
            size_ratio=round(ratio, 3),
            bold_ratio=base.bold_ratio,
            italic_ratio=base.italic_ratio,
            font=base.font,
        )


def build_heading_model(
    lines: list[RawLine],
    options: ParseOptions,
    body_lines: list[RawLine] | None = None,
) -> HeadingModel:
    """Learn the type hierarchy of one document.

    ``body_lines`` restricts the body-size vote to a subset -- in practice the
    lines outside the header and footer bands -- while ``lines`` still supplies
    the candidate styles.
    """
    body = body_size(body_lines if body_lines is not None else lines)
    bold_lines = sum(1 for ln in lines if ln.bold_ratio() >= 0.6)
    bold_saturated = bool(lines) and bold_lines / len(lines) > _BOLD_SATURATION

    candidates = [ln for ln in lines if _is_candidate(ln, body, bold_saturated, options)]

    numbered = sum(1 for ln in candidates if heading_number_depth(ln.text) is not None)
    trust_numbering = numbered >= 2 and numbered >= 0.4 * max(len(candidates), 1)

    keys = {(round(ln.dominant_size(), 1), ln.bold_ratio() >= 0.6) for ln in candidates}
    levels = rank_levels(keys, options)

    return HeadingModel(
        body=body,
        levels=levels,
        bold_saturated=bold_saturated,
        trust_numbering=trust_numbering,
        options=options,
    )


def rank_levels(
    keys: set[tuple[float, bool]], options: ParseOptions
) -> dict[tuple[float, bool], int]:
    """Map each distinct heading type style to a level.

    Bigger first; at equal size, bold outranks regular. Absolute sizes never
    matter, only their order, so a document set entirely in 9pt gets the same
    hierarchy as one set in 14pt.
    """
    ranked = sorted(keys, key=lambda k: (-k[0], not k[1]))
    return {key: min(index + 1, options.max_heading_level) for index, key in enumerate(ranked)}


def _is_candidate(
    line: RawLine, body: float, bold_saturated: bool, options: ParseOptions
) -> bool:
    return is_heading_candidate(
        line.text,
        line.dominant_size(),
        line.bold_ratio() >= 0.6,
        body,
        bold_saturated,
        options,
    )


def is_heading_candidate(
    raw_text: str,
    size: float,
    is_bold: bool,
    body: float,
    bold_saturated: bool,
    options: ParseOptions,
) -> bool:
    """Whether a piece of text could be a heading, on typography and shape.

    Split out from :class:`HeadingModel` so the DOCX reader can apply the same
    rules to documents that use direct formatting instead of named styles.
    """
    text = raw_text.strip()
    if not text or len(text) > options.max_heading_chars:
        return False

    ratio = size / body if body else 1.0
    bold = is_bold and not bold_saturated
    numbered = heading_number_depth(text) is not None
    #: Set apart from body text by size or weight. Everything below turns on
    #: this: a heading always looks different, a list item does not.
    distinct = bold or ratio >= options.min_heading_ratio

    marker = split_list_marker(text)
    if marker is not None:
        _, _, ordered = marker
        if not ordered:
            # A bullet is a list item even when the whole list is set in bold.
            return False
        # "1. Install the package" and "1. Introduction" are the same string.
        # Only typography separates a numbered heading from a numbered item.
        if not distinct:
            return False

    # Prose stays prose unless it is dramatically larger than the body, which
    # is the one case where a long line really is a title.
    if is_sentence_like(text) and ratio < _OVERRIDE_RATIO:
        return False

    if ratio >= options.min_heading_ratio:
        return True
    if bold and ratio >= 0.98:
        return True
    if numbered and distinct:
        return True
    # Small caps and all-caps section labels are often set at body size.
    return (
        ratio >= 0.98
        and len(text) <= _MAX_CAPS_CHARS
        and text.upper() == text
        and any(ch.isalpha() for ch in text)
    )


def normalize_levels(levels: list[int], options: ParseOptions) -> list[int]:
    """Close gaps in the set of heading levels a document actually uses.

    A document using only levels 1, 3 and 4 becomes 1, 2 and 3. Retrieval
    systems that reconstruct a section path from heading depth break on gaps,
    and the gap carries no information the document intended.

    The remapping is global rather than positional, so one type style always
    maps to one level no matter where in the document it appears -- which a
    running "never skip more than one" walk would not guarantee.
    """
    if not options.normalize_heading_levels or not levels:
        return levels
    dense = {level: rank + 1 for rank, level in enumerate(sorted(set(levels)))}
    return [min(dense[level], options.max_heading_level) for level in levels]
