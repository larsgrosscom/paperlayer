"""PDF reader.

pdfplumber is used for exactly two things: positioned characters
(``page.chars``) and vector edges (``page.edges``). Everything structural --
grouping characters into words, words into lines, detecting superscripts,
recovering tables -- is done here and in :mod:`paperlayer.pipeline`, because
the built-in extractors throw away the font metrics that heading detection
depends on and flatten tables into ambiguous text.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from typing import Any, BinaryIO

from .._text import is_bold_font, is_italic_font, normalize_unicode
from ..errors import MissingBackendError, ParseError, PasswordRequiredError
from ..options import ParseOptions
from ..types import BBox, SourceInfo
from .base import RawDocument, RawLine, RawPage, RawWord

__all__ = ["Edge", "read_pdf"]


class Edge:
    """A vector rule on the page, normalised to horizontal or vertical."""

    __slots__ = ("bottom", "orientation", "top", "x0", "x1")

    def __init__(
        self, x0: float, top: float, x1: float, bottom: float, orientation: str
    ) -> None:
        self.x0 = x0
        self.top = top
        self.x1 = x1
        self.bottom = bottom
        self.orientation = orientation

    @property
    def length(self) -> float:
        return (self.x1 - self.x0) if self.orientation == "h" else (self.bottom - self.top)

    @property
    def y(self) -> float:
        """Representative y for a horizontal edge."""
        return (self.top + self.bottom) / 2.0

    @property
    def x(self) -> float:
        """Representative x for a vertical edge."""
        return (self.x0 + self.x1) / 2.0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Edge {self.orientation} "
            f"({self.x0:.1f},{self.top:.1f})-({self.x1:.1f},{self.bottom:.1f})>"
        )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def read_pdf(
    source: str | os.PathLike[str] | bytes | BinaryIO,
    options: ParseOptions,
) -> tuple[RawDocument, dict[int, list[Edge]]]:
    """Read a PDF into intermediate form.

    Returns the document plus the vector edges per page, which the table stage
    needs and which are not worth carrying on :class:`RawPage` for DOCX.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depends on install shape
        raise MissingBackendError("pdfplumber", "pdf") from exc

    from pdfminer.pdfdocument import (
        PDFPasswordIncorrect,
    )

    stream: Any = source
    if isinstance(source, bytes):
        import io

        stream = io.BytesIO(source)

    path = (
        os.fspath(source)
        if isinstance(source, (str, os.PathLike))
        else getattr(source, "name", None)
    )

    try:
        pdf_ctx = pdfplumber.open(stream, password=options.password or "")
    except PDFPasswordIncorrect as exc:
        raise PasswordRequiredError(
            "The PDF is encrypted and the supplied password was rejected. "
            "Pass the correct one with parse(..., password=...)."
        ) from exc
    except Exception as exc:  # pdfminer raises a wide range of parse errors
        raise ParseError(f"Could not open the PDF: {exc}") from exc

    warnings: list[str] = []
    pages: list[RawPage] = []
    edges_by_page: dict[int, list[Edge]] = {}

    with pdf_ctx as pdf:
        total = len(pdf.pages)
        first, last = _page_range(options, total)
        blank_pages: list[int] = []

        for index in range(first - 1, last):
            page = pdf.pages[index]
            number = index + 1
            try:
                chars = page.chars
                raw_edges = page.edges
            except Exception as exc:  # a single broken page must not kill the run
                warnings.append(f"page {number}: could not read content ({exc})")
                page.close()
                continue

            lines = _build_lines(chars, number, options)
            if not lines:
                blank_pages.append(number)

            pages.append(
                RawPage(
                    number=number,
                    width=float(page.width or 612.0),
                    height=float(page.height or 792.0),
                    lines=lines,
                )
            )
            edges_by_page[number] = _normalize_edges(raw_edges)
            # pdfplumber caches per-page objects; a long document otherwise
            # holds every page in memory at once.
            page.close()

        if blank_pages:
            warnings.append(
                f"{len(blank_pages)} page(s) contained no extractable text "
                f"(scanned images?): {_summarize_pages(blank_pages)}"
            )

        source_info = SourceInfo(
            format="pdf",
            path=path,
            n_pages=total,
            title=_meta(pdf.metadata, "Title"),
            author=_meta(pdf.metadata, "Author"),
            producer=_meta(pdf.metadata, "Producer"),
        )

    return RawDocument(pages=pages, source=source_info, warnings=warnings), edges_by_page


def _page_range(options: ParseOptions, total: int) -> tuple[int, int]:
    if options.pages is None:
        return 1, total
    first, last = options.pages
    return max(1, first), min(total, last)


def _summarize_pages(numbers: Sequence[int], limit: int = 8) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (", ..." if len(numbers) > limit else "")


def _meta(metadata: dict[str, Any] | None, key: str) -> str | None:
    """Pull a metadata string, tolerating the bytes and PSLiteral pdfminer emits."""
    if not metadata:
        return None
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, bytes):
        decoded: str | None = None
        for encoding in ("utf-16", "utf-8", "latin-1"):
            try:
                decoded = value.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:  # pragma: no cover - latin-1 never fails
            return None
        value = decoded
    text = str(value).strip()
    return text or None


# --------------------------------------------------------------------------
# Edges
# --------------------------------------------------------------------------


def _normalize_edges(raw_edges: Iterable[dict[str, Any]]) -> list[Edge]:
    """Keep only edges thin enough and long enough to be ruling lines.

    Page borders, underlines and struck-through text all show up here too; the
    table stage filters further using connectivity, so this only drops noise
    that could never be part of a grid.
    """
    out: list[Edge] = []
    for e in raw_edges:
        try:
            x0 = float(e["x0"])
            x1 = float(e["x1"])
            top = float(e["top"])
            bottom = float(e["bottom"])
        except (KeyError, TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if bottom < top:
            top, bottom = bottom, top
        width = x1 - x0
        height = bottom - top
        orientation = e.get("orientation")
        if orientation not in ("h", "v"):
            orientation = "h" if width >= height else "v"
        if orientation == "h":
            if width < 6.0 or height > 3.0:
                continue
        else:
            if height < 6.0 or width > 3.0:
                continue
        out.append(Edge(x0, top, x1, bottom, orientation))
    return out


# --------------------------------------------------------------------------
# Characters -> words -> lines
# --------------------------------------------------------------------------


def _char_size(c: dict[str, Any]) -> float:
    size = c.get("size")
    try:
        value = float(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 0.0
    if value > 0.1:
        return value
    try:
        return max(float(c["bottom"]) - float(c["top"]), 1.0)
    except (KeyError, TypeError, ValueError):
        return 10.0


def _build_lines(
    chars: Iterable[dict[str, Any]], page_no: int, options: ParseOptions
) -> list[RawLine]:
    """Cluster characters into visual lines, then into words."""
    usable = [c for c in chars if _usable_char(c)]
    if not usable:
        return []

    clusters = _cluster_into_lines(usable, options.line_tolerance_ratio)

    lines: list[RawLine] = []
    for cluster in clusters:
        words = _cluster_to_words(cluster, options)
        if not words:
            continue
        box = BBox.union([w.bbox for w in words])
        assert box is not None  # words is non-empty
        lines.append(RawLine(words=words, page=page_no, bbox=box))

    lines.sort(key=lambda ln: (ln.bbox.top, ln.bbox.x0))
    return lines


def _usable_char(c: dict[str, Any]) -> bool:
    text = c.get("text")
    if not text:
        return False
    # Rotated text (table headers set sideways, watermarks) cannot be placed in
    # a linear reading order; dropping it beats interleaving it wrongly.
    if not c.get("upright", True):
        return False
    try:
        float(c["x0"]), float(c["x1"]), float(c["top"]), float(c["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _cluster_into_lines(
    chars: list[dict[str, Any]], tol_ratio: float
) -> list[list[dict[str, Any]]]:
    """Greedy vertical clustering on the character centre line.

    Characters arrive sorted by vertical centre, so a single pass suffices: a
    character either belongs to the run being accumulated or opens a new one.
    Superscripts and mixed font sizes are caught by the overlap test, which is
    more forgiving than a pure centre-distance test.
    """
    decorated = sorted(
        (((float(c["top"]) + float(c["bottom"])) / 2.0, float(c["x0"]), c) for c in chars),
        key=lambda t: (t[0], t[1]),
    )

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    cur_cy = 0.0
    cur_top = 0.0
    cur_bottom = 0.0
    cur_size = 0.0

    for cy, _x0, c in decorated:
        size = _char_size(c)
        top = float(c["top"])
        bottom = float(c["bottom"])
        if current:
            tol = max(1.0, tol_ratio * max(size, cur_size))
            overlap = min(bottom, cur_bottom) - max(top, cur_top)
            min_height = max(min(bottom - top, cur_bottom - cur_top), 0.1)
            if abs(cy - cur_cy) <= tol or overlap >= 0.6 * min_height:
                current.append(c)
                cur_cy += (cy - cur_cy) / len(current)
                cur_top = min(cur_top, top)
                cur_bottom = max(cur_bottom, bottom)
                cur_size = max(cur_size, size)
                continue
            clusters.append(current)
        current = [c]
        cur_cy, cur_top, cur_bottom, cur_size = cy, top, bottom, size

    if current:
        clusters.append(current)
    return clusters


def _cluster_to_words(cluster: list[dict[str, Any]], options: ParseOptions) -> list[RawWord]:
    """Split one line of characters into words.

    Three signals break a word, because PDFs disagree about which they provide:
    an explicit space glyph, a horizontal gap wider than ``word_gap_ratio`` of
    the font size, and a change in superscript state. Relying on gaps alone
    breaks on fonts with very narrow spaces; relying on space glyphs alone
    breaks on the many producers that position every word absolutely. The
    superscript break is what keeps a footnote reference from being welded to
    the word in front of it, since ``text1`` and ``text 1`` are otherwise the
    same string.
    """
    chars = sorted(cluster, key=lambda c: float(c["x0"]))
    _flag_superscripts(chars)

    words: list[RawWord] = []
    pending: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    prev_was_space = False

    def flush() -> None:
        nonlocal pending
        if pending:
            word = _make_word(pending, options)
            if word is not None:
                words.append(word)
            pending = []

    for c in chars:
        text = str(c["text"])
        if text.isspace():
            flush()
            prev = c
            prev_was_space = True
            continue
        if prev is not None:
            if prev_was_space or bool(c.get(_SUP_KEY)) != bool(prev.get(_SUP_KEY)):
                flush()
            else:
                gap = float(c["x0"]) - float(prev["x1"])
                threshold = options.word_gap_ratio * max(_char_size(c), _char_size(prev))
                if gap > threshold:
                    flush()
        pending.append(c)
        prev = c
        prev_was_space = False

    flush()
    return words


#: Scratch key stamped onto pdfplumber character dicts by _flag_superscripts.
_SUP_KEY = "_paperlayer_sup"


def _flag_superscripts(chars: list[dict[str, Any]]) -> None:
    """Mark characters set smaller and raised relative to their line.

    Done per character rather than per word because a footnote reference is
    usually glued to the preceding word with no intervening space; by the time
    the text is a string the distinction is unrecoverable.
    """
    weights: dict[float, int] = {}
    for c in chars:
        if str(c["text"]).isspace():
            continue
        key = round(_char_size(c), 1)
        weights[key] = weights.get(key, 0) + 1
    if not weights:
        return
    body_size = max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0]

    baselines = sorted(
        float(c["bottom"])
        for c in chars
        if not str(c["text"]).isspace() and abs(_char_size(c) - body_size) < 0.6
    )
    if not baselines:
        return
    baseline = baselines[len(baselines) // 2]

    for c in chars:
        raised = baseline - float(c["bottom"]) >= 0.10 * body_size
        c[_SUP_KEY] = _char_size(c) < 0.88 * body_size and raised


def _make_word(chars: list[dict[str, Any]], options: ParseOptions) -> RawWord | None:
    text = "".join(str(c["text"]) for c in chars)
    if options.normalize_unicode:
        text = normalize_unicode(text)
    text = text.strip()
    if not text:
        return None

    x0 = min(float(c["x0"]) for c in chars)
    x1 = max(float(c["x1"]) for c in chars)
    top = min(float(c["top"]) for c in chars)
    bottom = max(float(c["bottom"]) for c in chars)

    # Character-weighted mode, so one oversized glyph cannot redefine the word.
    size_weights: dict[float, int] = {}
    font_weights: dict[str, int] = {}
    for c in chars:
        size_weights[round(_char_size(c), 1)] = size_weights.get(round(_char_size(c), 1), 0) + 1
        name = str(c.get("fontname") or "")
        font_weights[name] = font_weights.get(name, 0) + 1

    size = max(size_weights.items(), key=lambda kv: (kv[1], kv[0]))[0]
    font = max(font_weights.items(), key=lambda kv: kv[1])[0] if font_weights else ""

    return RawWord(
        text=text,
        bbox=BBox(x0, top, x1, bottom),
        size=size,
        font=font,
        bold=is_bold_font(font),
        italic=is_italic_font(font),
        superscript=all(bool(c.get(_SUP_KEY)) for c in chars),
    )
