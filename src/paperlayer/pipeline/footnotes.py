"""Footnote recovery and attachment.

Footnotes are the piece most extractors get worst: the note text lands at the
bottom of the page, detached from the sentence that needed it, and the
reference digit is silently glued onto the preceding word. Both halves are
recovered here -- the note by its position and reduced type size, the reference
by the superscript flag the reader preserved -- and then reunited, so the note
travels in the same retrieval chunk as the claim it supports.
"""

from __future__ import annotations

import re

from .._text import looks_like_page_number
from ..options import ParseOptions
from ..readers.base import RawLine, RawPage
from ..types import Block, Footnote

__all__ = ["attach_footnotes", "extract_footnotes", "marker_of"]

#: Footnote type is set smaller than body type; this is the cutoff.
_SIZE_CUTOFF = 0.94
#: Footnotes live in the bottom part of the page, never higher than this.
_ZONE_START = 0.55
#: Symbols used as footnote markers where numbering would collide with content.
_SYMBOLS = "*†‡§¶#"

_MARKER_RE = re.compile(rf"^(\d{{1,3}}|[{re.escape(_SYMBOLS)}]{{1,3}})[.)\]]?\s+(\S.*)$")
_MARKER_ONLY_RE = re.compile(rf"^(\d{{1,3}}|[{re.escape(_SYMBOLS)}]{{1,3}})$")


def marker_of(word_text: str) -> str | None:
    """The bare marker inside a superscript token, if it looks like one."""
    stripped = word_text.strip().strip(".,;:)]")
    if not stripped or len(stripped) > 3:
        return None
    return stripped if _MARKER_ONLY_RE.match(stripped) else None


def extract_footnotes(page: RawPage, body: float, options: ParseOptions) -> list[Footnote]:
    """Pull footnotes off the bottom of a page, removing them from the flow.

    Two conditions must both hold, which is what keeps a small-print legal
    paragraph from being mistaken for footnotes: the text is set below body
    size, and it sits below every line that is at body size. Real footnotes are
    always at the foot.
    """
    if not options.detect_footnotes or not page.lines:
        return []

    body_bottom = max(
        (ln.bbox.bottom for ln in page.lines if ln.dominant_size() > _SIZE_CUTOFF * body),
        default=0.0,
    )
    zone_start = max(body_bottom, _ZONE_START * page.height)

    candidates = [
        ln
        for ln in page.lines
        if ln.bbox.top >= zone_start - 1.0
        and ln.dominant_size() <= _SIZE_CUTOFF * body
        and ln.text.strip()
        # A page number sits in the same zone at the same reduced size, and
        # would otherwise be swallowed as the last line of the final note.
        and not looks_like_page_number(ln.text)
    ]
    if not candidates:
        return []

    candidates.sort(key=lambda ln: (ln.bbox.top, ln.bbox.x0))
    if _split_marker(candidates[0]) is None:
        # The block at the foot is small print, but it is not a numbered note.
        return []

    notes: list[Footnote] = []
    consumed: list[RawLine] = []
    current_marker: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_marker, current_lines
        if current_marker is not None and current_lines:
            notes.append(
                Footnote(
                    marker=current_marker,
                    text=" ".join(current_lines).strip(),
                    page=page.number,
                )
            )
        current_marker, current_lines = None, []

    for line in candidates:
        split = _split_marker(line)
        if split is not None:
            flush()
            current_marker, rest = split
            current_lines = [rest] if rest else []
        elif current_marker is not None:
            current_lines.append(line.text.strip())
        else:
            continue
        consumed.append(line)

    flush()

    if notes:
        drop = {id(ln) for ln in consumed}
        page.lines = [ln for ln in page.lines if id(ln) not in drop]
    return notes


def _split_marker(line: RawLine) -> tuple[str, str] | None:
    """``(marker, rest)`` if this line opens a footnote definition."""
    if not line.words:
        return None

    # A superscript leading token is the strongest evidence available.
    first = line.words[0]
    if first.superscript:
        marker = marker_of(first.text)
        if marker is not None:
            rest = " ".join(w.text for w in line.words[1:]).strip()
            return (marker, rest) if rest else None

    match = _MARKER_RE.match(line.text.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None


def attach_footnotes(
    blocks: list[Block], notes_by_page: dict[int, list[Footnote]], options: ParseOptions
) -> None:
    """Attach each footnote to the block whose text references it.

    Notes whose reference could not be found -- a common outcome when the
    reference sits inside a table cell or an image -- fall back to the last
    block on their page, so nothing is silently dropped.
    """
    if not options.detect_footnotes or not notes_by_page:
        return

    remaining = {
        page: {note.marker: note for note in notes} for page, notes in notes_by_page.items()
    }

    for block in blocks:
        if block.page is None:
            continue
        pending = remaining.get(block.page)
        if not pending:
            continue
        for marker in _referenced_markers(block.text):
            note = pending.pop(marker, None)
            if note is not None:
                block.footnotes.append(note)

    # Whatever is left over goes to the last block of its page.
    last_on_page: dict[int, Block] = {}
    for block in blocks:
        if block.page is not None:
            last_on_page[block.page] = block

    for page, pending in remaining.items():
        if not pending:
            continue
        host = last_on_page.get(page)
        if host is None:
            continue
        for note in pending.values():
            host.footnotes.append(note)
            host.meta.setdefault("orphan_footnotes", []).append(note.marker)


_REF_RE = re.compile(r"\[\^([^\]]{1,3})\]")


def _referenced_markers(text: str) -> list[str]:
    return _REF_RE.findall(text)
