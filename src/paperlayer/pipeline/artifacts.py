"""Running headers, footers and page numbers.

The signal is repetition, not position: a line is furniture when the *same*
line, digits normalised away, shows up in the same band on most pages. That
catches ``Confidential Draft 4`` and ``Page 7 of 92`` alike, and leaves a
first-page-only subtitle sitting in the header band untouched.

Nothing is stripped from a single-page document. With one page there is no
repetition to observe, and guessing would cost real content.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise

from .._text import looks_like_page_number, normalize_for_repeat
from ..options import ParseOptions
from ..readers.base import RawDocument, RawLine, RawPage
from .captions import caption_label

__all__ = ["body_zone_lines", "strip_artifacts"]

#: Repeated content outside the header/footer bands is only furniture if it is
#: short; a repeated paragraph is boilerplate the user probably wants.
_MAX_FLOATING_CHARS = 80
#: ...and if it holds the same vertical position on every page it appears on.
_POSITION_TOLERANCE = 18.0
#: ...and if it is set smaller than body text.
_SMALL_PRINT = 0.95

_INT_RE = re.compile(r"^\D*(\d{1,4})\D*$")


def strip_artifacts(doc: RawDocument, options: ParseOptions, body_size: float = 0.0) -> None:
    """Tag and (unless ``keep_headers``) remove page furniture.

    Mutates the pages in place. Tagged lines carry ``line.artifact`` set to
    ``"header"``, ``"footer"`` or ``"page_number"``.
    """
    pages = doc.pages
    if len(pages) < 2:
        return

    zones = {page.number: _zones(page, options) for page in pages}

    _mark_repeated(pages, zones, options)
    _mark_page_numbers(pages, zones)
    _mark_floating_repeats(pages, options, body_size)

    if options.keep_headers:
        return

    removed = 0
    for page in pages:
        keep = [ln for ln in page.lines if ln.artifact is None]
        removed += len(page.lines) - len(keep)
        page.lines = keep
    if removed:
        doc.warnings.append(
            f"stripped {removed} running header/footer line(s); "
            f"pass keep_headers=True to retain them"
        )


def body_zone_lines(doc: RawDocument, options: ParseOptions) -> list[RawLine]:
    """Lines outside the header and footer bands.

    Used to estimate the body text size. Running headers are often set several
    points smaller than body text, and on a page with little prose they can win
    the vote outright and drag every real paragraph up into heading territory.
    """
    out: list[RawLine] = []
    for page in doc.pages:
        header_limit = options.header_band * page.height
        footer_limit = (1.0 - options.footer_band) * page.height
        for line in page.lines:
            if line.bbox.bottom <= header_limit or line.bbox.top >= footer_limit:
                continue
            out.append(line)
    return out or doc.all_lines()


def _zones(page: RawPage, options: ParseOptions) -> dict[str, list[RawLine]]:
    """Split a page into its header band, footer band and body."""
    header_limit = options.header_band * page.height
    footer_limit = (1.0 - options.footer_band) * page.height
    header: list[RawLine] = []
    footer: list[RawLine] = []
    for line in page.lines:
        if line.bbox.bottom <= header_limit:
            header.append(line)
        elif line.bbox.top >= footer_limit:
            footer.append(line)
    return {"header": header, "footer": footer}


def _threshold(n_pages: int, options: ParseOptions) -> int:
    """How many pages a line must appear on to count as furniture."""
    return max(
        options.artifact_min_pages,
        math.ceil(options.artifact_page_ratio * n_pages),
    )


def _mark_repeated(
    pages: list[RawPage],
    zones: dict[int, dict[str, list[RawLine]]],
    options: ParseOptions,
) -> None:
    """Mark band lines whose normalised text recurs across pages."""
    needed = _threshold(len(pages), options)

    for band in ("header", "footer"):
        occurrences: dict[str, list[tuple[int, RawLine]]] = {}
        for page in pages:
            for line in zones[page.number][band]:
                signature = normalize_for_repeat(line.text)
                if not signature:
                    continue
                occurrences.setdefault(signature, []).append((page.number, line))

        for hits in occurrences.values():
            distinct_pages = {page_no for page_no, _ in hits}
            if len(distinct_pages) >= needed:
                for _page_no, line in hits:
                    line.artifact = band


def _mark_page_numbers(
    pages: list[RawPage], zones: dict[int, dict[str, list[RawLine]]]
) -> None:
    """Mark bare page numbers, which never repeat and so escape ``_mark_repeated``.

    A number is only a page number if it *advances* with the page. That single
    check is what separates ``7`` in the footer of page 7 from a footnote
    reference or a stray figure number.
    """
    for band in ("header", "footer"):
        candidates: list[tuple[int, RawLine, int | None]] = []
        for page in pages:
            for line in zones[page.number][band]:
                if line.artifact is not None:
                    continue
                text = line.text.strip()
                if not looks_like_page_number(text):
                    continue
                match = _INT_RE.match(text)
                value = int(match.group(1)) if match else None
                candidates.append((page.number, line, value))

        if len(candidates) < 2:
            continue

        numbered = [(p, ln, v) for p, ln, v in candidates if v is not None]
        if len(numbered) >= 2 and _advances([(p, v) for p, _ln, v in numbered]):
            for _p, line, _v in numbered:
                line.artifact = "page_number"

        # Roman numerals and the like carry no comparable value, but a
        # non-numeric page-number-shaped line appearing in the same band on
        # most pages is still furniture.
        unnumbered = [(p, ln) for p, ln, v in candidates if v is None]
        if len(unnumbered) >= max(2, len(pages) // 2):
            for _p, line in unnumbered:
                line.artifact = "page_number"


def _advances(numbered: list[tuple[int, int]]) -> bool:
    """Whether the numbers grow with the page index, allowing small jumps."""
    values = [value for _page, value in sorted(numbered)]
    if len(values) < 2:
        return False
    increases = 0
    for prev, cur in pairwise(values):
        delta = cur - prev
        if delta <= 0 or delta > 3:
            return False
        increases += 1
    return increases >= 1


def _mark_floating_repeats(
    pages: list[RawPage], options: ParseOptions, body_size: float
) -> None:
    """Catch furniture that sits outside the bands, e.g. a side watermark.

    Matching is *exact* here, unlike in the bands. Collapsing digits is right
    for ``Page 3 of 40``, but applied to body text it makes ``Section 1`` and
    ``Section 2`` identical, and a document with numbered sections on every
    page would delete itself.
    """
    needed = _threshold(len(pages), options)
    occurrences: dict[str, list[RawLine]] = {}
    for page in pages:
        seen_on_page: set[str] = set()
        for line in page.lines:
            if line.artifact is not None:
                continue
            text = line.text.strip()
            if not text or len(text) > _MAX_FLOATING_CHARS:
                continue
            # Furniture outside the bands (watermarks, classification banners,
            # side rules) is set smaller than body text. Body-size prose that
            # happens to repeat is content, and deleting it is unrecoverable.
            if body_size and line.dominant_size() > _SMALL_PRINT * body_size:
                continue
            # A caption is content by definition, however often it recurs.
            if caption_label(text) is not None:
                continue
            signature = " ".join(text.lower().split())
            if not signature or signature in seen_on_page:
                continue
            seen_on_page.add(signature)
            occurrences.setdefault(signature, []).append(line)

    for lines in occurrences.values():
        if len(lines) < needed:
            continue
        # Furniture is positionally stable. A sentence that genuinely recurs in
        # the prose lands wherever the text flow puts it, so a repeated line
        # that also holds the same vertical position on every page is the one
        # worth deleting.
        tops = [line.bbox.top for line in lines]
        if max(tops) - min(tops) > _POSITION_TOLERANCE:
            continue
        for line in lines:
            line.artifact = "header"
