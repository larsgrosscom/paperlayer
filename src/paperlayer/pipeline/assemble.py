"""Turning a stream of classified lines into blocks.

This is where a paragraph stops being a set of coordinates and becomes one
chunk of text. The decisions that matter are all about *breaks*: which two
consecutive lines belong to the same paragraph and which do not. Getting that
wrong is what produces the classic RAG failure of a chunk that starts halfway
through one thought and ends halfway through the next.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypedDict

from .._text import join_hyphenated
from ..options import ParseOptions
from ..readers.base import RawLine, RawTable
from ..types import BBox, Block, TableData
from .captions import caption_label
from .footnotes import marker_of
from .headings import HeadingModel, normalize_levels
from .lists import IndentScale, ListItem, item_start
from .order import Element

__all__ = ["assemble"]


class ListItemPayload(TypedDict):
    """One entry of ``Block.meta["items"]`` for a list block."""

    text: str
    level: int
    marker: str
    ordered: bool


#: A line starting this far right of the paragraph edge is a new, indented
#: paragraph rather than a continuation.
_INDENT_BREAK = 8.0
#: A line ending this far short of the paragraph edge is a ragged final line.
_RAGGED_FRACTION = 0.15
#: Font size change, in points, that forces a paragraph break.
_SIZE_BREAK = 0.8
#: Sentence-final characters, for the ragged-end test.
_SENTENCE_END = (".", "!", "?", '"', "”", "’", ")")


def assemble(
    elements: Sequence[Element],
    model: HeadingModel,
    markers_by_page: dict[int, set[str]],
    options: ParseOptions,
) -> list[Block]:
    """Build the final block list from ordered lines and tables."""
    builder = _Builder(model, markers_by_page, options)
    for element in elements:
        builder.feed(element)
    builder.flush()
    blocks = builder.blocks

    _renumber_headings(blocks, options)
    for index, block in enumerate(blocks):
        block.order = index
    return blocks


class _Builder:
    """Accumulates lines into blocks, flushing whenever a break is detected."""

    def __init__(
        self,
        model: HeadingModel,
        markers_by_page: dict[int, set[str]],
        options: ParseOptions,
    ) -> None:
        self.model = model
        self.markers = markers_by_page
        self.options = options
        self.blocks: list[Block] = []
        self._para: list[tuple[RawLine, str]] = []
        self._items: list[ListItem] = []
        self._list_lines: list[RawLine] = []
        self._scale = IndentScale([])

    # -- entry points ---------------------------------------------------

    def feed(self, element: Element) -> None:
        if isinstance(element, RawTable):
            self.flush()
            self._emit_table(element)
        else:
            self._feed_line(element)

    def flush(self) -> None:
        self._flush_list()
        self._flush_paragraph()

    # -- line handling --------------------------------------------------

    def _feed_line(self, line: RawLine) -> None:
        text = self._line_text(line)
        if not text.strip():
            return

        if line.artifact is not None:
            # Only reachable with keep_headers=True; furniture is otherwise
            # gone by now. Kept as its own block so it never contaminates a
            # neighbouring paragraph.
            self.flush()
            self.blocks.append(
                Block(
                    type="paragraph",
                    text=text,
                    page=line.page,
                    bbox=line.bbox,
                    style=self.model.style_for(line),
                    meta={"artifact": line.artifact},
                )
            )
            return

        level = self.model.level_for(line)
        if level is not None:
            self.flush()
            if self._continues_heading(line, level):
                previous = self.blocks[-1]
                joined = (
                    join_hyphenated(previous.text, text) if self.options.dehyphenate else None
                )
                previous.text = joined if joined is not None else f"{previous.text} {text}"
                if previous.bbox is not None:
                    previous.bbox = previous.bbox.merge(line.bbox)
                return
            self.blocks.append(
                Block(
                    type="heading",
                    text=text,
                    page=line.page,
                    level=level,
                    bbox=line.bbox,
                    style=self.model.style_for(line),
                )
            )
            return

        if self.options.detect_lists:
            start = item_start(text)
            if start is not None:
                marker, remainder, ordered = start
                if self._items and not self._list_continues(line):
                    self._flush_list()
                self._flush_paragraph()
                self._items.append(
                    ListItem(
                        marker=marker,
                        ordered=ordered,
                        level=0,
                        lines=[remainder],
                        indent=line.bbox.x0,
                    )
                )
                self._list_lines.append(line)
                return
            if self._items and self._is_item_continuation(line):
                self._items[-1].lines.append(text)
                self._list_lines.append(line)
                return
            self._flush_list()

        if self._para and self._breaks_paragraph(line):
            self._flush_paragraph()
        self._para.append((line, text))

    def _line_text(self, line: RawLine) -> str:
        """Render a line, converting footnote references to ``[^n]``.

        A superscript token only becomes a reference when a footnote with that
        marker was actually found on the same page. Without that check every
        squared exponent in the document turns into a dangling footnote.
        """
        markers: frozenset[str] | set[str] = self.markers.get(line.page, frozenset())
        parts: list[str] = []
        for word in line.words:
            if word.superscript and markers:
                marker = marker_of(word.text)
                if marker is not None and marker in markers:
                    if parts:
                        parts[-1] = parts[-1] + f"[^{marker}]"
                    else:
                        parts.append(f"[^{marker}]")
                    continue
            parts.append(word.text)
        return " ".join(parts)

    def _continues_heading(self, line: RawLine, level: int) -> bool:
        """Whether this line is the wrapped continuation of the heading above.

        A long title set across two lines is one heading, not two. The test is
        deliberately tight -- same level, same type size and weight, same page,
        and a gap no larger than normal line spacing -- because merging two
        genuinely separate headings would destroy the document outline.
        """
        if not self.blocks:
            return False
        previous = self.blocks[-1]
        if previous.type != "heading" or previous.level != level:
            return False
        if previous.page != line.page or previous.bbox is None:
            return False

        style = self.model.style_for(line)
        if previous.style is None:
            return False
        if abs(previous.style.size - style.size) > 0.3:
            return False
        if previous.style.is_bold != style.is_bold:
            return False

        gap = line.bbox.top - previous.bbox.bottom
        if gap < -1.0 or gap > 0.8 * max(line.height, 1.0):
            return False
        # A heading that already reads as a complete sentence is finished.
        return not previous.text.rstrip().endswith((".", "!", "?", ":"))

    # -- paragraph breaks -----------------------------------------------

    def _breaks_paragraph(self, line: RawLine) -> bool:
        prev, _ = self._para[-1]

        if line.page != prev.page or line.column != prev.column:
            return True

        gap = line.bbox.top - prev.bbox.bottom
        height = max(prev.height, line.height, 1.0)
        if gap > self.options.para_gap_ratio * height:
            return True

        if abs(line.dominant_size() - prev.dominant_size()) > _SIZE_BREAK:
            return True
        if (line.bold_ratio() >= 0.6) != (prev.bold_ratio() >= 0.6):
            return True

        left = min(ln.bbox.x0 for ln, _ in self._para)
        right = max(ln.bbox.x1 for ln, _ in self._para)
        width = right - left

        # A short last line ending a sentence is how a paragraph ends; the next
        # line therefore starts a new one.
        if (
            width > 0
            and prev.bbox.x1 < right - _RAGGED_FRACTION * width
            and prev.text.rstrip().endswith(_SENTENCE_END)
        ):
            return True

        # A first-line indent is an explicit paragraph marker.
        return line.bbox.x0 > left + _INDENT_BREAK

    def _flush_paragraph(self) -> None:
        if not self._para:
            return
        lines = [ln for ln, _ in self._para]
        text = _join((t for _, t in self._para), self.options)
        self._para = []
        if not text:
            return

        block_type = "paragraph"
        meta: dict[str, object] = {}
        if self.options.detect_captions:
            label = caption_label(text)
            if label is not None:
                kind, name = label
                block_type = "caption"
                meta = {"caption_kind": kind, "label": name}

        self.blocks.append(
            Block(
                type=block_type,  # type: ignore[arg-type]
                text=text,
                page=lines[0].page,
                bbox=BBox.union([ln.bbox for ln in lines]),
                style=self.model.style_for(lines[0]),
                meta=meta,
            )
        )

    # -- lists ------------------------------------------------------------

    def _list_continues(self, line: RawLine) -> bool:
        if not self._list_lines:
            return False
        prev = self._list_lines[-1]
        if line.page != prev.page or line.column != prev.column:
            return False
        gap = line.bbox.top - prev.bbox.bottom
        height = max(prev.height, line.height, 1.0)
        return gap <= self.options.para_gap_ratio * height * 1.6

    def _is_item_continuation(self, line: RawLine) -> bool:
        """A wrapped line of the current item, rather than new prose.

        The test is the left edge: a continuation is indented past the marker
        it belongs to, while a new paragraph returns to the margin.
        """
        if not self._list_lines:
            return False
        if not self._list_continues(line):
            return False
        return line.bbox.x0 > self._items[-1].indent + 2.0

    def _flush_list(self) -> None:
        if not self._items:
            return
        items, lines = self._items, self._list_lines
        self._items, self._list_lines = [], []

        scale = IndentScale([item.indent for item in items])
        for item in items:
            item.level = scale.level(item.indent)

        payload: list[ListItemPayload] = [
            {
                "text": item.text,
                "level": item.level,
                "marker": item.marker,
                "ordered": item.ordered,
            }
            for item in items
            if item.text
        ]
        if not payload:
            return

        text = "\n".join(
            "  " * entry["level"]
            + (f"{entry['marker']}. " if entry["ordered"] else "- ")
            + entry["text"]
            for entry in payload
        )
        self.blocks.append(
            Block(
                type="list",
                text=text,
                page=lines[0].page if lines else None,
                level=max(entry["level"] for entry in payload) + 1,
                bbox=BBox.union([ln.bbox for ln in lines]),
                style=self.model.style_for(lines[0]) if lines else None,
                meta={"items": payload},
            )
        )

    # -- tables -----------------------------------------------------------

    def _emit_table(self, table: RawTable) -> None:
        rows = [list(row) for row in table.rows]
        if not rows:
            return
        header = rows[0] if table.has_header else None
        data = rows[1:] if table.has_header else rows
        self.blocks.append(
            Block(
                type="table",
                text="\n".join("\t".join(cell for cell in row) for row in rows),
                page=table.page,
                bbox=table.bbox,
                table=TableData(rows=data, header=header, ruled=table.ruled),
                meta={
                    "ruled": table.ruled,
                    "n_rows": len(rows),
                    "n_cols": max(len(r) for r in rows),
                },
            )
        )


def _join(texts: Iterable[str], options: ParseOptions) -> str:
    """Join the lines of a paragraph, healing words split across a break."""
    out = ""
    for text in texts:
        chunk = text.strip()
        if not chunk:
            continue
        if not out:
            out = chunk
            continue
        joined = join_hyphenated(out, chunk) if options.dehyphenate else None
        out = joined if joined is not None else f"{out} {chunk}"
    return out.strip()


def _renumber_headings(blocks: list[Block], options: ParseOptions) -> None:
    headings = [b for b in blocks if b.type == "heading"]
    if not headings:
        return
    levels = normalize_levels([b.level or 1 for b in headings], options)
    for block, level in zip(headings, levels, strict=True):
        block.level = level
