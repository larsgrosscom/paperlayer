"""Column detection and reading order.

Text extracted straight off a two-column page interleaves the columns line by
line, which is the single most damaging failure mode for a RAG pipeline: every
sentence is spliced with an unrelated one. This module finds the gutters,
splits lines that were wrongly merged across them, and emits page content in
the order a human would read it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..options import ParseOptions
from ..readers.base import RawDocument, RawLine, RawPage, RawTable
from ..types import BBox

__all__ = ["Element", "detect_columns", "reading_order"]

Element = RawLine | RawTable

#: Column index meaning "spans every column" -- a banner heading or wide table.
FULL_WIDTH = -1

#: Below this many lines a page has no statistical basis for column detection.
_MIN_LINES = 6
#: A gutter must be at least this fraction of the page width.
_MIN_GUTTER_FRACTION = 0.028
#: ...and at least this many points, for narrow pages.
_MIN_GUTTER_POINTS = 12.0
#: Fraction of lines allowed to cross a gutter and still leave it a gutter.
_CROSSING_TOLERANCE = 0.18
#: Ignore candidate gutters this close to the content edges.
_EDGE_MARGIN = 0.12
#: Most documents are one or two columns; three is the practical ceiling.
_MAX_COLUMNS = 3


def detect_columns(page: RawPage, options: ParseOptions) -> None:
    """Assign a column index to every line, splitting wrongly merged ones.

    Mutates ``page`` in place: ``page.lines`` may grow (a line merged across
    two columns becomes two lines) and ``page.n_columns`` is set.
    """
    page.n_columns = 1
    for line in page.lines:
        line.column = 0

    if not options.detect_columns or len(page.lines) < _MIN_LINES:
        return

    gutters = _find_gutters(page)
    if not gutters:
        return

    bounds = _column_bounds(page, gutters)
    new_lines: list[RawLine] = []
    assignments: list[int] = []

    for line in page.lines:
        if _crosses(line, gutters):
            line.column = FULL_WIDTH
            new_lines.append(line)
            assignments.append(FULL_WIDTH)
            continue
        pieces = _split_line(line, bounds)
        for column, piece in pieces:
            piece.column = column
            new_lines.append(piece)
            assignments.append(column)

    if not _columns_are_balanced(assignments, len(bounds)):
        for line in page.lines:
            line.column = 0
        return

    page.lines = sorted(new_lines, key=lambda ln: (ln.bbox.top, ln.bbox.x0))
    page.n_columns = len(bounds)


def _find_gutters(page: RawPage) -> list[tuple[float, float]]:
    """Vertical bands that almost no line occupies."""
    lines = page.lines
    x0 = min(ln.bbox.x0 for ln in lines)
    x1 = max(ln.bbox.x1 for ln in lines)
    span = x1 - x0
    if span < 100.0:
        return []

    counts = _occupancy(lines, x0, x1)
    allowed = int(_CROSSING_TOLERANCE * len(lines))
    min_width = max(_MIN_GUTTER_POINTS, _MIN_GUTTER_FRACTION * page.width)
    margin = _EDGE_MARGIN * span

    runs: list[tuple[float, float]] = []
    start: int | None = None
    for i, count in enumerate([*counts, 10**9]):
        if count <= allowed:
            if start is None:
                start = i
        elif start is not None:
            a, b = x0 + start, x0 + i
            if b - a >= min_width and a >= x0 + margin and b <= x1 - margin:
                runs.append((a, b))
            start = None

    if not runs:
        return []
    runs.sort(key=lambda r: r[1] - r[0], reverse=True)
    return sorted(runs[: _MAX_COLUMNS - 1])


def _occupancy(lines: Sequence[RawLine], x0: float, x1: float) -> list[int]:
    """Per-point count of how many lines cover each horizontal position."""
    n = max(int(x1 - x0) + 1, 1)
    counts = [0] * n
    for line in lines:
        for w in line.words:
            a = max(0, int(w.bbox.x0 - x0))
            b = min(n - 1, int(w.bbox.x1 - x0))
            for k in range(a, b + 1):
                counts[k] += 1
    return counts


def _column_bounds(
    page: RawPage, gutters: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    edges = [-1e9]
    for a, b in gutters:
        edges.append((a + b) / 2.0)
    edges.append(1e9)
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _crosses(line: RawLine, gutters: list[tuple[float, float]]) -> bool:
    """Whether any word physically sits in a gutter.

    This is the test that separates a banner heading (a word straddles the
    gutter) from two column lines that merely share a vertical position (no
    word ever enters the gutter).
    """
    for a, b in gutters:
        for w in line.words:
            if w.bbox.x0 < b and w.bbox.x1 > a:
                return True
    return False


def _split_line(line: RawLine, bounds: list[tuple[float, float]]) -> list[tuple[int, RawLine]]:
    out: list[tuple[int, RawLine]] = []
    for index, (lo, hi) in enumerate(bounds):
        words = line.words_in_range(lo, hi)
        if not words:
            continue
        box = BBox.union([w.bbox for w in words])
        assert box is not None
        out.append((index, RawLine(words=words, page=line.page, bbox=box, column=index)))
    return out


def _columns_are_balanced(assignments: list[int], n_columns: int) -> bool:
    """Reject a split where one column holds nearly everything.

    A lone right-aligned marginal note would otherwise be promoted to a column
    and reorder the whole page around it.
    """
    body = [a for a in assignments if a != FULL_WIDTH]
    if len(body) < _MIN_LINES:
        return False
    for index in range(n_columns):
        share = sum(1 for a in body if a == index) / len(body)
        if share < 0.15:
            return False
    return True


def reading_order(doc: RawDocument, options: ParseOptions) -> list[Element]:
    """Flatten the document into one ordered stream of lines and tables."""
    elements: list[Element] = []
    for page in doc.pages:
        elements.extend(_order_page(page))
    return elements


def _order_page(page: RawPage) -> list[Element]:
    items: list[tuple[float, float, int, Element]] = []
    for line in page.lines:
        items.append((line.bbox.top, line.bbox.x0, line.column, line))
    for table in page.tables:
        items.append((table.bbox.top, table.bbox.x0, _table_column(page, table), table))

    items.sort(key=lambda t: (t[0], t[1]))

    if page.n_columns <= 1:
        return [item[3] for item in items]

    ordered: list[Element] = []
    buckets: list[list[Element]] = [[] for _ in range(page.n_columns)]

    def flush() -> None:
        for bucket in buckets:
            ordered.extend(bucket)
            bucket.clear()

    for _top, _x0, column, element in items:
        if column == FULL_WIDTH or column >= page.n_columns:
            # A full-width element ends every column above it and starts a new
            # band below, which is exactly how a reader treats a banner.
            flush()
            ordered.append(element)
        else:
            buckets[column].append(element)
    flush()
    return ordered


def _table_column(page: RawPage, table: RawTable) -> int:
    """Which column a table belongs to, or ``FULL_WIDTH`` if it spans them."""
    if page.n_columns <= 1:
        return 0
    columns = {ln.column for ln in page.lines if ln.bbox.h_overlap(table.bbox) > 0}
    columns.discard(FULL_WIDTH)
    if len(columns) == 1:
        return next(iter(columns))
    return FULL_WIDTH
