"""Every tunable knob in one place.

Defaults are chosen so that ``parse("file.pdf")`` with no arguments produces
the output you would want for a RAG index: artifacts stripped, tables as
Markdown, footnotes kept and attached to their referencing block.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

__all__ = ["FootnoteMode", "ParseOptions", "TableMode"]

#: How table blocks are rendered.
#:
#: ``markdown``
#:     GitHub-flavoured pipe tables. Compact and the best fit for embeddings.
#: ``html``
#:     ``<table>`` markup. The only mode that can express merged cells.
#: ``csv``
#:     A fenced ``csv`` code block. Useful when a downstream step re-parses it.
#: ``text``
#:     Tab-separated rows, no fence. Cheapest in tokens.
#: ``drop``
#:     Omit table blocks from the Markdown entirely (they stay in ``.blocks``).
TableMode = Literal["markdown", "html", "csv", "text", "drop"]

#: Where footnote definitions end up.
#:
#: ``inline``
#:     ``[^1]`` marker in the text, definition rendered right after the block
#:     that references it. Keeps the footnote in the same retrieval chunk.
#: ``end``
#:     Markers inline, all definitions collected at the end of the document.
#: ``drop``
#:     Markers and definitions removed from the Markdown (kept in ``.blocks``).
FootnoteMode = Literal["inline", "end", "drop"]


@dataclass(slots=True)
class ParseOptions:
    """Parsing and rendering configuration.

    Passed to :func:`paperlayer.parse` as keyword arguments; you rarely need to
    build one by hand.
    """

    # Content selection
    #: Keep running headers, footers and page numbers instead of stripping
    #: them. When True they are emitted as paragraphs tagged
    #: ``meta["artifact"] = "header" | "footer" | "page_number"``.
    keep_headers: bool = False
    table_mode: TableMode = "markdown"
    footnote_mode: FootnoteMode = "inline"
    #: Restrict parsing to a 1-based inclusive page range, e.g. ``(1, 10)``.
    pages: tuple[int, int] | None = None
    #: Password for encrypted PDFs.
    password: str | None = None

    # Detection toggles
    detect_headings: bool = True
    detect_tables: bool = True
    #: Recover tables that are aligned with whitespace only, with no ruling
    #: lines. Higher recall, lower precision than ruled-table detection.
    detect_unruled_tables: bool = True
    detect_lists: bool = True
    detect_captions: bool = True
    detect_footnotes: bool = True
    #: Detect multi-column page layouts and read column-by-column instead of
    #: straight across the page.
    detect_columns: bool = True

    # Text normalisation
    #: Join words split across a line break by a trailing hyphen.
    dehyphenate: bool = True
    #: Expand ligatures, normalise dashes and quotes, drop zero-width marks.
    normalize_unicode: bool = True
    #: Escape Markdown control characters that would otherwise change the
    #: structure of the output (a paragraph starting with ``#``, for example).
    escape_markdown: bool = True

    # Heading tuning
    #: Minimum ``size / body_size`` ratio for a line to be considered a
    #: heading on size alone. Bold lines qualify at a lower ratio.
    min_heading_ratio: float = 1.12
    #: Deepest heading level emitted; deeper candidates collapse into it.
    max_heading_level: int = 6
    #: Longest line (in characters) still eligible to be a heading.
    max_heading_chars: int = 200
    #: Rewrite detected levels so they never jump by more than one at a time
    #: (an h1 followed by an h3 becomes an h1 followed by an h2).
    normalize_heading_levels: bool = True

    # Artifact tuning
    #: Fraction of the page height treated as the header zone.
    header_band: float = 0.10
    #: Fraction of the page height treated as the footer zone.
    footer_band: float = 0.12
    #: A repeated line must appear on at least this fraction of pages before
    #: it is treated as a running header or footer.
    artifact_page_ratio: float = 0.6
    #: ...and on at least this many pages, so short documents are not damaged.
    artifact_min_pages: int = 3

    # Layout tuning
    #: Word break threshold as a fraction of font size: a horizontal gap wider
    #: than this between two characters starts a new word.
    word_gap_ratio: float = 0.22
    #: Gap wider than this (as a fraction of font size) is a cell or column
    #: separator rather than a word space.
    cell_gap_ratio: float = 1.6
    #: Two characters belong to the same line when their vertical centres are
    #: within this fraction of the font size.
    line_tolerance_ratio: float = 0.45
    #: Vertical gap between lines, relative to line height, above which a new
    #: paragraph starts.
    para_gap_ratio: float = 1.45

    def replace(self, **changes: Any) -> ParseOptions:
        """A copy with some fields changed."""
        return replace(self, **changes)

    def validate(self) -> ParseOptions:
        """Raise :class:`ValueError` on nonsensical settings; return self."""
        if self.table_mode not in ("markdown", "html", "csv", "text", "drop"):
            raise ValueError(
                f"table_mode must be one of markdown, html, csv, text, drop; "
                f"got {self.table_mode!r}"
            )
        if self.footnote_mode not in ("inline", "end", "drop"):
            raise ValueError(
                f"footnote_mode must be one of inline, end, drop; got {self.footnote_mode!r}"
            )
        if not 1 <= self.max_heading_level <= 6:
            raise ValueError("max_heading_level must be between 1 and 6")
        if self.min_heading_ratio < 1.0:
            raise ValueError("min_heading_ratio must be >= 1.0")
        if not 0.0 <= self.header_band < 0.5 or not 0.0 <= self.footer_band < 0.5:
            raise ValueError("header_band and footer_band must be in [0.0, 0.5)")
        if not 0.0 < self.artifact_page_ratio <= 1.0:
            raise ValueError("artifact_page_ratio must be in (0.0, 1.0]")
        if self.pages is not None:
            first, last = self.pages
            if first < 1 or last < first:
                raise ValueError(
                    "pages must be a 1-based inclusive (first, last) range "
                    "with first >= 1 and last >= first"
                )
        return self
