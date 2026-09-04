"""Table reconstruction.

Two independent detectors, run in that order:

``ruled``
    Recovers the grid from vector ruling lines. Reliable enough to trust its
    cell boundaries verbatim, including horizontally merged cells.
``unruled``
    Falls back to whitespace alignment for tables drawn with nothing but
    spacing. Deliberately strict -- a run of lines must share a column gap that
    is empty on *every* line -- because a false positive here turns readable
    prose into a mangled grid, which is far worse than missing a table.

Neither uses pdfplumber table extraction; both work off the same word and edge
primitives the rest of the pipeline sees.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise
from typing import TYPE_CHECKING

from ..options import ParseOptions
from ..readers.base import RawLine, RawPage, RawTable, RawWord
from ..types import BBox

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..readers.pdf import Edge

__all__ = ["apply_ruled_tables", "apply_unruled_tables", "looks_like_header"]

#: Coordinates closer than this are the same grid line.
_SNAP_TOL = 3.0
#: Fraction of a cell border that must actually be drawn for it to count.
_BORDER_COVERAGE = 0.55
#: Edges within this distance are part of the same drawing.
_COMPONENT_TOL = 4.0
#: Guard against pathological vector art (maps, charts) with thousands of edges.
_MAX_EDGES = 4000


# --------------------------------------------------------------------------
# Ruled tables
# --------------------------------------------------------------------------


def apply_ruled_tables(page: RawPage, edges: Sequence[Edge], options: ParseOptions) -> None:
    """Find ruled tables on a page and remove their words from the text flow."""
    if not options.detect_tables or not edges or not page.lines:
        return
    if len(edges) > _MAX_EDGES:
        return

    words = [(line_i, w) for line_i, line in enumerate(page.lines) for w in line.words]
    if not words:
        return

    consumed: set[int] = set()
    for component in _components(edges):
        table = _table_from_component(component, words, consumed, page.number, options)
        if table is not None:
            page.tables.append(table)

    if consumed:
        _drop_words(page, consumed)


def _components(edges: Sequence[Edge]) -> list[list[Edge]]:
    """Group edges that touch into candidate drawings, via union-find."""
    n = len(edges)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    boxes = [
        (
            e.x0 - _COMPONENT_TOL,
            e.top - _COMPONENT_TOL,
            e.x1 + _COMPONENT_TOL,
            e.bottom + _COMPONENT_TOL,
        )
        for e in edges
    ]
    for i in range(n):
        ax0, atop, ax1, abottom = boxes[i]
        for j in range(i + 1, n):
            bx0, btop, bx1, bbottom = boxes[j]
            if ax1 < bx0 or bx1 < ax0 or abottom < btop or bbottom < atop:
                continue
            union(i, j)

    groups: dict[int, list[Edge]] = {}
    for i, edge in enumerate(edges):
        groups.setdefault(find(i), []).append(edge)
    return list(groups.values())


def _table_from_component(
    component: list[Edge],
    words: list[tuple[int, RawWord]],
    consumed: set[int],
    page_no: int,
    options: ParseOptions,
) -> RawTable | None:
    h_edges = [e for e in component if e.orientation == "h"]
    v_edges = [e for e in component if e.orientation == "v"]
    if len(h_edges) < 2 or len(v_edges) < 2:
        return None

    ys = _snap([e.y for e in h_edges])
    xs = _snap([e.x for e in v_edges])
    if len(ys) < 3 and len(xs) < 3:
        # Fewer than 2 rows *and* fewer than 2 columns: a box, not a table.
        return None
    if len(ys) < 2 or len(xs) < 2:
        return None

    n_rows = len(ys) - 1
    n_cols = len(xs) - 1
    if n_rows < 1 or n_cols < 2 or n_rows * n_cols < 4:
        return None

    bbox = BBox(xs[0], ys[0], xs[-1], ys[-1])
    if bbox.width < 20.0 or bbox.height < 10.0:
        return None

    # Assign every word to the cell containing its centre.
    grid: list[list[list[tuple[int, RawWord]]]] = [
        [[] for _ in range(n_cols)] for _ in range(n_rows)
    ]
    hits = 0
    for idx, (_line_i, w) in enumerate(words):
        if idx in consumed:
            continue
        cx, cy = w.bbox.cx, w.bbox.cy
        if not (xs[0] - 1 <= cx <= xs[-1] + 1 and ys[0] - 1 <= cy <= ys[-1] + 1):
            continue
        col = _bucket(xs, cx)
        row = _bucket(ys, cy)
        if col is None or row is None:
            continue
        grid[row][col].append((idx, w))
        hits += 1

    if hits == 0:
        return None

    rows: list[list[str]] = []
    bold_ratios: list[float] = []
    touched: set[int] = set()

    for r in range(n_rows):
        cells: list[str] = []
        c = 0
        while c < n_cols:
            span = 1
            # Extend right while the border between this cell and the next was
            # never drawn: that is a horizontally merged cell.
            while c + span < n_cols and not _covered(
                v_edges, xs[c + span], ys[r], ys[r + 1], vertical=True
            ):
                span += 1
            merged = [item for cc in range(c, c + span) for item in grid[r][cc]]
            text = _cell_text(w for _, w in merged)
            cells.append(text)
            cells.extend([""] * (span - 1))
            touched.update(idx for idx, _ in merged)
            c += span
        rows.append(cells)
        row_words = [w for cc in range(n_cols) for _, w in grid[r][cc]]
        bold_ratios.append(_bold_ratio(row_words))

    rows = _drop_empty_edges(rows)
    if not rows or len(rows) < 1:
        return None
    if sum(1 for row in rows for cell in row if cell) < 2:
        return None

    consumed.update(touched)
    return RawTable(
        rows=rows,
        bbox=bbox,
        page=page_no,
        ruled=True,
        has_header=looks_like_header(rows, bold_ratios),
    )


def _bucket(bounds: list[float], value: float) -> int | None:
    """Index of the band in ``bounds`` containing ``value``."""
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        if lo - 1.0 <= value < hi + 1.0:
            return i
    return None


def _snap(values: Iterable[float], tol: float = _SNAP_TOL) -> list[float]:
    """Collapse near-identical coordinates into single grid lines."""
    vals = sorted(values)
    if not vals:
        return []
    out: list[float] = []
    group = [vals[0]]
    for v in vals[1:]:
        if v - group[-1] <= tol:
            group.append(v)
        else:
            out.append(sum(group) / len(group))
            group = [v]
    out.append(sum(group) / len(group))
    return out


def _covered(
    edges: Sequence[Edge],
    coord: float,
    start: float,
    end: float,
    *,
    vertical: bool,
    tol: float = _SNAP_TOL,
) -> bool:
    """Whether a border is actually drawn along ``[start, end]`` at ``coord``."""
    span = end - start
    if span <= 0:
        return False
    intervals: list[tuple[float, float]] = []
    for e in edges:
        pos = e.x if vertical else e.y
        if abs(pos - coord) > tol:
            continue
        a = max(e.top if vertical else e.x0, start)
        b = min(e.bottom if vertical else e.x1, end)
        if b > a:
            intervals.append((a, b))
    if not intervals:
        return False
    intervals.sort()
    covered = 0.0
    cur_a, cur_b = intervals[0]
    for a, b in intervals[1:]:
        if a > cur_b:
            covered += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    covered += cur_b - cur_a
    return covered >= _BORDER_COVERAGE * span


def _cell_text(words: Iterable[RawWord]) -> str:
    """Join the words of one cell, preserving its internal line breaks."""
    items = sorted(words, key=lambda w: (round(w.bbox.cy, 1), w.bbox.x0))
    if not items:
        return ""
    lines: list[list[RawWord]] = [[items[0]]]
    for w in items[1:]:
        prev = lines[-1][-1]
        if abs(w.bbox.cy - prev.bbox.cy) <= 0.5 * max(w.size, prev.size, 1.0):
            lines[-1].append(w)
        else:
            lines.append([w])
    return "\n".join(" ".join(w.text for w in line) for line in lines).strip()


def _bold_ratio(words: Sequence[RawWord]) -> float:
    total = sum(len(w.text) for w in words)
    if not total:
        return 0.0
    return sum(len(w.text) for w in words if w.bold) / total


def _drop_empty_edges(rows: list[list[str]]) -> list[list[str]]:
    """Trim all-empty leading/trailing rows and all-empty columns."""
    while rows and not any(c.strip() for c in rows[0]):
        rows.pop(0)
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    rows = [r + [""] * (n_cols - len(r)) for r in rows]
    keep = [c for c in range(n_cols) if any(r[c].strip() for r in rows)]
    if len(keep) < n_cols:
        rows = [[r[c] for c in keep] for r in rows]
    return rows


def looks_like_header(rows: list[list[str]], bold_ratios: list[float]) -> bool:
    """Whether the first row is a header rather than data.

    Two signals: it is set bold while the body is not, or it is entirely
    non-numeric while some later row carries numbers.
    """
    if len(rows) < 2:
        return False
    if bold_ratios and bold_ratios[0] >= 0.6:
        rest = bold_ratios[1:]
        if not rest or sum(rest) / len(rest) < 0.5:
            return True

    def numeric_cells(row: Sequence[str]) -> int:
        return sum(1 for c in row if _is_numeric(c))

    if numeric_cells(rows[0]) == 0 and any(numeric_cells(r) >= 1 for r in rows[1:]):
        filled = sum(1 for c in rows[0] if c.strip())
        return filled >= max(2, len(rows[0]) // 2)
    return False


def _is_numeric(cell: str) -> bool:
    stripped = cell.strip().replace(",", "").replace(" ", "")
    stripped = stripped.lstrip("$€£+-").rstrip("%")
    if not stripped:
        return False
    try:
        float(stripped)
    except ValueError:
        return False
    return True


def _drop_words(page: RawPage, consumed: set[int]) -> None:
    """Remove table words from the running text and rebuild the affected lines."""
    idx = 0
    new_lines: list[RawLine] = []
    for line in page.lines:
        keep: list[RawWord] = []
        for w in line.words:
            if idx not in consumed:
                keep.append(w)
            idx += 1
        if not keep:
            continue
        box = BBox.union([w.bbox for w in keep])
        assert box is not None
        line.words = keep
        line.bbox = box
        new_lines.append(line)
    page.lines = new_lines


# --------------------------------------------------------------------------
# Unruled tables
# --------------------------------------------------------------------------

#: A column gutter must be at least this wide, in points.
_MIN_GUTTER = 9.0
#: ...and at least this many lines must share it.
_MIN_TABLE_ROWS = 3


def apply_unruled_tables(page: RawPage, options: ParseOptions) -> None:
    """Recover whitespace-aligned tables from the remaining lines of a page."""
    if not options.detect_tables or not options.detect_unruled_tables:
        return
    if len(page.lines) < _MIN_TABLE_ROWS:
        return

    by_column: dict[int, list[int]] = {}
    for i, line in enumerate(page.lines):
        by_column.setdefault(line.column, []).append(i)

    consumed: set[int] = set()
    tables: list[RawTable] = []
    for indices in by_column.values():
        tables.extend(_scan_column(page, indices, consumed, options))

    if not tables:
        return
    page.tables.extend(tables)
    page.lines = [ln for i, ln in enumerate(page.lines) if i not in consumed]


def _scan_column(
    page: RawPage,
    indices: list[int],
    consumed: set[int],
    options: ParseOptions,
) -> list[RawTable]:
    lines = [page.lines[i] for i in indices]
    tables: list[RawTable] = []
    i = 0
    while i < len(lines) - _MIN_TABLE_ROWS + 1:
        best_end = -1
        best_gutters: list[tuple[float, float]] = []
        # Grow the window while the candidate columns survive.
        for end in range(i + _MIN_TABLE_ROWS, len(lines) + 1):
            window = lines[i:end]
            if not _vertically_contiguous(window, options):
                break
            gutters = _shared_gutters(window, options)
            if not gutters:
                break
            best_end, best_gutters = end, gutters
        if best_end < 0:
            i += 1
            continue

        window = lines[i:best_end]
        rows = [_split_by_gutters(ln, best_gutters) for ln in window]
        if _plausible_grid(rows) and not _is_page_layout(window, best_gutters, page):
            bbox = BBox.union([ln.bbox for ln in window])
            assert bbox is not None
            bold = [_bold_ratio(ln.words) for ln in window]
            tables.append(
                RawTable(
                    rows=rows,
                    bbox=bbox,
                    page=page.number,
                    ruled=False,
                    has_header=looks_like_header(rows, bold),
                )
            )
            consumed.update(indices[i:best_end])
            i = best_end
        else:
            i += 1
    return tables


#: A gap this wide running down most of the page is a column gutter.
_LAYOUT_GUTTER = 18.0
_LAYOUT_HEIGHT_SHARE = 0.6


def _is_page_layout(
    window: Sequence[RawLine], gutters: list[tuple[float, float]], page: RawPage
) -> bool:
    """Whether this is really a multi-column page that column detection missed.

    A wide gap running down most of the page is a gutter, not a table border.
    Reading it as a table would splice unrelated sentences into the same row,
    so on this particular ambiguity it is better to emit nothing.
    """
    top = min(ln.bbox.top for ln in window)
    bottom = max(ln.bbox.bottom for ln in window)
    if bottom - top < _LAYOUT_HEIGHT_SHARE * page.height:
        return False
    return any(b - a >= _LAYOUT_GUTTER for a, b in gutters)


def _vertically_contiguous(window: Sequence[RawLine], options: ParseOptions) -> bool:
    """Reject windows with a paragraph break in the middle."""
    for prev, cur in pairwise(window):
        gap = cur.bbox.top - prev.bbox.bottom
        height = max(prev.height, cur.height, 1.0)
        if gap > options.para_gap_ratio * height * 1.5:
            return False
    return True


def _shared_gutters(
    window: Sequence[RawLine], options: ParseOptions
) -> list[tuple[float, float]]:
    """Horizontal bands that are empty on every line of the window.

    Requiring emptiness on *every* line, not most, is what keeps ragged prose
    from being read as a table.
    """
    x0 = min(ln.bbox.x0 for ln in window)
    x1 = max(ln.bbox.x1 for ln in window)
    if x1 - x0 < 40.0:
        return []

    free: list[tuple[float, float]] = [(x0, x1)]
    for line in window:
        occupied = [(w.bbox.x0, w.bbox.x1) for w in line.words]
        free = _subtract(free, occupied)
        if not free:
            return []

    gutters = [
        (a, b)
        for a, b in free
        # An interior gutter only; leading and trailing whitespace is margin.
        if b - a >= _MIN_GUTTER and a > x0 + 1.0 and b < x1 - 1.0
    ]
    if not gutters:
        return []

    # Every line must actually straddle at least one gutter, otherwise we are
    # looking at a hanging indent rather than columns.
    straddling = 0
    for line in window:
        if any(
            any(w.bbox.x1 <= a for w in line.words) and any(w.bbox.x0 >= b for w in line.words)
            for a, b in gutters
        ):
            straddling += 1
    if straddling < len(window) - 1 or straddling < _MIN_TABLE_ROWS - 1:
        return []
    return gutters


def _subtract(
    free: list[tuple[float, float]], occupied: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Interval subtraction: ``free`` minus everything in ``occupied``."""
    for ox0, ox1 in occupied:
        nxt: list[tuple[float, float]] = []
        for a, b in free:
            if ox1 <= a or ox0 >= b:
                nxt.append((a, b))
                continue
            if ox0 > a:
                nxt.append((a, ox0))
            if ox1 < b:
                nxt.append((ox1, b))
        free = nxt
        if not free:
            break
    return free


def _split_by_gutters(line: RawLine, gutters: list[tuple[float, float]]) -> list[str]:
    bounds = [-1e9] + [(a + b) / 2.0 for a, b in gutters] + [1e9]
    cells: list[str] = []
    for i in range(len(bounds) - 1):
        words = line.words_in_range(bounds[i], bounds[i + 1])
        cells.append(" ".join(w.text for w in words).strip())
    return cells


def _plausible_grid(rows: list[list[str]]) -> bool:
    """Reject grids too sparse or too degenerate to be a real table."""
    if len(rows) < _MIN_TABLE_ROWS:
        return False
    n_cols = max(len(r) for r in rows)
    if n_cols < 2:
        return False
    total = len(rows) * n_cols
    filled = sum(1 for r in rows for c in r if c.strip())
    if filled / total < 0.6:
        return False
    # At least two rows must use more than one column, or this is a list with
    # an unlucky indent rather than a table.
    multi = sum(1 for r in rows if sum(1 for c in r if c.strip()) >= 2)
    return multi >= max(2, len(rows) - 1)
