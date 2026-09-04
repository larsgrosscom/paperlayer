"""The structured document model produced by :func:`paperlayer.parse`.

Everything here is a plain dataclass with no third-party dependency, so this
module is cheap to import and safe to pickle, ``asdict``, or ship over a wire.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .options import ParseOptions

__all__ = [
    "BBox",
    "Block",
    "BlockType",
    "Document",
    "Footnote",
    "SourceInfo",
    "StyleInfo",
    "TableData",
]

BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "table",
    "caption",
    "footnote",
    "code",
]

#: Block types that carry prose and are worth embedding on their own.
TEXT_BLOCK_TYPES: frozenset[str] = frozenset({"paragraph", "list", "caption", "code"})


@dataclass(frozen=True, slots=True)
class BBox:
    """A bounding box in PDF user space, origin at the top-left of the page.

    ``top``/``bottom`` grow downwards, matching pdfplumber coordinate space, so
    ``top < bottom`` for a well-formed box.
    """

    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def cx(self) -> float:
        """Horizontal centre."""
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        """Vertical centre."""
        return (self.top + self.bottom) / 2.0

    def merge(self, other: BBox) -> BBox:
        """Smallest box containing both."""
        return BBox(
            min(self.x0, other.x0),
            min(self.top, other.top),
            max(self.x1, other.x1),
            max(self.bottom, other.bottom),
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.top <= y <= self.bottom

    def v_overlap(self, other: BBox) -> float:
        """Vertical overlap in points (negative when the boxes are disjoint)."""
        return min(self.bottom, other.bottom) - max(self.top, other.top)

    def h_overlap(self, other: BBox) -> float:
        """Horizontal overlap in points (negative when disjoint)."""
        return min(self.x1, other.x1) - max(self.x0, other.x0)

    @staticmethod
    def union(boxes: list[BBox]) -> BBox | None:
        it = iter(boxes)
        try:
            acc = next(it)
        except StopIteration:
            return None
        for b in it:
            acc = acc.merge(b)
        return acc


@dataclass(frozen=True, slots=True)
class StyleInfo:
    """Typographic evidence behind the classification of a block.

    Kept on every block so you can inspect *why* something became an ``h2``
    instead of reverse-engineering it from the Markdown.
    """

    size: float = 0.0
    #: ``size`` divided by the body-text size of the document. The primary
    #: signal for heading detection: 1.0 means "same size as body text".
    size_ratio: float = 1.0
    #: Fraction of characters set in a bold face, 0.0-1.0.
    bold_ratio: float = 0.0
    italic_ratio: float = 0.0
    font: str | None = None

    @property
    def is_bold(self) -> bool:
        return self.bold_ratio >= 0.6

    @property
    def is_italic(self) -> bool:
        return self.italic_ratio >= 0.6


@dataclass(frozen=True, slots=True)
class Footnote:
    """A footnote, attached to the block whose text references it."""

    marker: str
    text: str
    page: int | None = None


@dataclass(frozen=True, slots=True)
class TableData:
    """A reconstructed table.

    ``rows`` never includes the header row; when ``header`` is ``None`` the
    table had no detectable header and every row is data.
    """

    rows: list[list[str]] = field(default_factory=list)
    header: list[str] | None = None
    #: True when the table was recovered from ruling lines rather than from
    #: whitespace alignment. Ruled tables are considerably more reliable.
    ruled: bool = False

    @property
    def n_cols(self) -> int:
        widths = [len(r) for r in self.rows]
        if self.header is not None:
            widths.append(len(self.header))
        return max(widths, default=0)

    @property
    def n_rows(self) -> int:
        return len(self.rows) + (1 if self.header is not None else 0)

    def all_rows(self) -> list[list[str]]:
        """Header (if any) followed by the data rows."""
        return ([self.header] if self.header is not None else []) + self.rows


@dataclass(slots=True)
class Block:
    """One structural unit of a document, the natural chunk for a RAG index."""

    type: BlockType
    #: Normalised plain text. For tables this is a tab-separated flattening,
    #: useful for embedding; use ``markdown`` for display.
    text: str
    #: 1-based page number. ``None`` for DOCX, which has no fixed pagination.
    page: int | None = None
    #: Position in reading order across the whole document, always ascending.
    order: int = 0
    #: Heading level 1-6, or the deepest nesting depth of a list block.
    level: int | None = None
    bbox: BBox | None = None
    style: StyleInfo | None = None
    table: TableData | None = None
    #: Footnotes referenced from the text of this block via ``[^marker]``.
    footnotes: list[Footnote] = field(default_factory=list)
    #: Type-specific extras: list items, caption labels, detection confidence.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        """This block alone, rendered as Markdown, with default options."""
        from .options import ParseOptions
        from .render.markdown import render_block

        return render_block(self, ParseOptions())

    def render(self, options: ParseOptions) -> str:
        """This block alone, rendered with explicit options."""
        from .render.markdown import render_block

        return render_block(self, options)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable view, including the rendered Markdown."""
        data = asdict(self)
        data["markdown"] = self.markdown
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        preview = self.text[:60].replace("\n", " ")
        if len(self.text) > 60:
            preview += "..."
        lvl = f" level={self.level}" if self.level is not None else ""
        pg = f" page={self.page}" if self.page is not None else ""
        return f"<Block {self.type}{lvl}{pg} {preview!r}>"


@dataclass(slots=True)
class SourceInfo:
    """Where the document came from and what the container claimed about it."""

    format: Literal["pdf", "docx"]
    path: str | None = None
    n_pages: int | None = None
    title: str | None = None
    author: str | None = None
    producer: str | None = None


@dataclass(slots=True)
class Document:
    """The result of a parse: ordered blocks plus one rendered Markdown view."""

    blocks: list[Block] = field(default_factory=list)
    source: SourceInfo = field(default_factory=lambda: SourceInfo(format="pdf"))
    #: Non-fatal problems, e.g. "3 pages contained no extractable text".
    warnings: list[str] = field(default_factory=list)
    #: The options this document was parsed with, so ``markdown`` is reproducible.
    options: ParseOptions | None = None

    @property
    def markdown(self) -> str:
        from .options import ParseOptions
        from .render.markdown import render_document

        return render_document(self, self.options or ParseOptions())

    @property
    def text(self) -> str:
        """Plain text of every block, newline separated. No Markdown syntax."""
        return "\n\n".join(b.text for b in self.blocks if b.text)

    def of_type(self, *types: str) -> list[Block]:
        """Blocks matching any of the given block types, in reading order."""
        wanted = set(types)
        return [b for b in self.blocks if b.type in wanted]

    def headings(self) -> list[Block]:
        return self.of_type("heading")

    def tables(self) -> list[TableData]:
        return [b.table for b in self.blocks if b.table is not None]

    def footnotes(self) -> list[Footnote]:
        return [fn for b in self.blocks for fn in b.footnotes]

    def outline(self) -> list[tuple[int, str]]:
        """``(level, text)`` for every heading, as a cheap table of contents."""
        return [(b.level or 1, b.text) for b in self.blocks if b.type == "heading"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "source": asdict(self.source),
            "warnings": list(self.warnings),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def __iter__(self) -> Iterator[Block]:
        return iter(self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Document {self.source.format} blocks={len(self.blocks)} "
            f"pages={self.source.n_pages} warnings={len(self.warnings)}>"
        )
