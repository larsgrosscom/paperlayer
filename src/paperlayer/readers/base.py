"""The intermediate representation that sits between readers and the pipeline.

A reader turns a file into :class:`RawDocument`: pages of positioned lines with
font metrics attached. Everything above this layer (heading detection, artifact
stripping, tables, footnotes) works only on these types and never
touches pdfplumber or python-docx, which is what keeps the analysis testable
without a real file on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..types import BBox, SourceInfo, StyleInfo

__all__ = [
    "RawDocument",
    "RawLine",
    "RawPage",
    "RawTable",
    "RawWord",
    "Reader",
]


@dataclass(slots=True)
class RawWord:
    """A run of characters with no internal whitespace, and its typography."""

    text: str
    bbox: BBox
    size: float
    font: str = ""
    bold: bool = False
    italic: bool = False
    #: Raised and shrunk relative to the surrounding line, which usually means
    #: a footnote reference marker.
    superscript: bool = False

    def __len__(self) -> int:
        return len(self.text)


@dataclass(slots=True)
class RawLine:
    """One visual line of text, as a sequence of words in reading order."""

    words: list[RawWord]
    page: int
    bbox: BBox
    #: Index of the layout column this line belongs to; 0 for single-column.
    column: int = 0
    #: Set by the artifact stage: "header", "footer" or "page_number".
    artifact: str | None = None

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words if w.text)

    @property
    def is_empty(self) -> bool:
        return not any(w.text.strip() for w in self.words)

    @property
    def height(self) -> float:
        return self.bbox.height

    def char_count(self) -> int:
        return sum(len(w.text) for w in self.words)

    def dominant_size(self) -> float:
        """Font size covering the most characters on this line.

        A character-weighted mode rather than a mean: a single large drop cap
        should not drag the size of an entire paragraph line upwards.
        """
        weights: dict[float, int] = {}
        for w in self.words:
            if w.superscript:
                continue  # footnote markers are always small; ignore them
            key = round(w.size, 1)
            weights[key] = weights.get(key, 0) + len(w.text)
        if not weights:
            return max((w.size for w in self.words), default=0.0)
        return max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def dominant_font(self) -> str:
        weights: dict[str, int] = {}
        for w in self.words:
            weights[w.font] = weights.get(w.font, 0) + len(w.text)
        if not weights:
            return ""
        return max(weights.items(), key=lambda kv: kv[1])[0]

    def _ratio(self, attr: str) -> float:
        total = self.char_count()
        if not total:
            return 0.0
        hit = sum(len(w.text) for w in self.words if getattr(w, attr))
        return hit / total

    def bold_ratio(self) -> float:
        return self._ratio("bold")

    def italic_ratio(self) -> float:
        return self._ratio("italic")

    def style(self) -> StyleInfo:
        """Typography of this line, with ``size_ratio`` left at 1.0.

        The ratio needs a document-wide body size, which only the heading stage
        knows, so it is filled in there.
        """
        return StyleInfo(
            size=self.dominant_size(),
            size_ratio=1.0,
            bold_ratio=self.bold_ratio(),
            italic_ratio=self.italic_ratio(),
            font=self.dominant_font() or None,
        )

    def gaps(self) -> list[tuple[float, float, int]]:
        """Horizontal gaps between consecutive words.

        Returns ``(gap_start_x, gap_end_x, index_of_word_after_gap)``. Column
        and table-cell detection both work off these.
        """
        out: list[tuple[float, float, int]] = []
        for i in range(1, len(self.words)):
            left = self.words[i - 1].bbox.x1
            right = self.words[i].bbox.x0
            if right > left:
                out.append((left, right, i))
        return out

    def words_in_range(self, x0: float, x1: float) -> list[RawWord]:
        """Words whose horizontal centre falls inside ``[x0, x1)``."""
        return [w for w in self.words if x0 <= w.bbox.cx < x1]


@dataclass(slots=True)
class RawTable:
    """A table recovered from a page, before Markdown rendering."""

    rows: list[list[str]]
    bbox: BBox
    page: int
    #: True when reconstructed from ruling lines, False when from whitespace.
    ruled: bool = False
    #: Whether ``rows[0]`` is a header row rather than data.
    has_header: bool = False
    #: Set by the caption stage when a nearby caption names this table.
    caption: str | None = None

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass(slots=True)
class RawPage:
    """One page of positioned content."""

    number: int
    width: float
    height: float
    lines: list[RawLine] = field(default_factory=list)
    tables: list[RawTable] = field(default_factory=list)
    #: Number of layout columns detected on this page.
    n_columns: int = 1


@dataclass(slots=True)
class RawDocument:
    """A whole document in intermediate form."""

    pages: list[RawPage] = field(default_factory=list)
    source: SourceInfo = field(default_factory=lambda: SourceInfo(format="pdf"))
    warnings: list[str] = field(default_factory=list)

    def all_lines(self) -> list[RawLine]:
        return [line for page in self.pages for line in page.lines]


class Reader(Protocol):
    """What every format backend implements."""

    def __call__(
        self, source: object, options: object
    ) -> RawDocument: ...  # pragma: no cover - structural type
