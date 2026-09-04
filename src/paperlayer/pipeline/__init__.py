"""The analysis pipeline: positioned lines in, structured blocks out.

Stage order is load-bearing and worth stating plainly, because most of it is
forced by dependencies between stages:

1. **Ruled tables** first, while the vector edges are still available and
   before any words have been consumed by anything else.
2. **Columns**, which need the full set of remaining words on a page to find
   the gutters, and which fix lines wrongly merged across them.
3. **Body size**, a character-weighted mode, so a handful of header lines set
   in small type cannot move it.
4. **Footnotes**, which need the body size to know what "smaller" means, and
   which run before artifact stripping so that a document repeating the same
   footnote on every page keeps it as a footnote rather than losing it as
   furniture.
5. **Artifacts**, which need every page at once to see what repeats.
6. **Unruled tables** on whatever text is left. After footnotes, or a block of
   notes gets read as a two-column table.
7. **Reading order**, then block assembly, then footnote attachment.

Every stage is a pure function of the intermediate representation, so any of
them can be exercised in a test without a file on disk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ..options import ParseOptions
from ..readers.base import RawDocument
from ..types import Block
from .artifacts import body_zone_lines, strip_artifacts
from .assemble import assemble
from .footnotes import attach_footnotes, extract_footnotes
from .headings import build_heading_model
from .order import detect_columns, reading_order
from .tables import apply_ruled_tables, apply_unruled_tables

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..readers.pdf import Edge

__all__ = ["build_blocks"]

#: A caption must sit within this many points of the table it names.
_CAPTION_DISTANCE = 72.0


def build_blocks(
    raw: RawDocument,
    edges_by_page: Mapping[int, Sequence[Edge]] | None,
    options: ParseOptions,
) -> list[Block]:
    """Run the full pipeline over a document in intermediate form."""
    edges_by_page = edges_by_page or {}

    for page in raw.pages:
        apply_ruled_tables(page, edges_by_page.get(page.number, []), options)

    for page in raw.pages:
        detect_columns(page, options)

    model = build_heading_model(
        raw.all_lines(), options, body_lines=body_zone_lines(raw, options)
    )

    notes_by_page = {}
    for page in raw.pages:
        notes = extract_footnotes(page, model.body, options)
        if notes:
            notes_by_page[page.number] = notes

    strip_artifacts(raw, options, body_size=model.body)
    markers_by_page = {
        page: {note.marker for note in notes} for page, notes in notes_by_page.items()
    }

    for page in raw.pages:
        apply_unruled_tables(page, options)

    # Rebuild against what actually remains: footnotes, furniture and table
    # cells are no longer competing with body text for the "what size is
    # normal" vote.
    model = build_heading_model(
        raw.all_lines(), options, body_lines=body_zone_lines(raw, options)
    )

    elements = reading_order(raw, options)
    blocks = assemble(elements, model, markers_by_page, options)

    attach_footnotes(blocks, notes_by_page, options)
    link_captions(blocks)
    return blocks


def link_captions(blocks: list[Block]) -> None:
    """Cross-reference caption blocks with the table they describe.

    Only table captions can be resolved: a figure caption names an image, and
    images are not part of the text stream at all.
    """
    tables = [b for b in blocks if b.type == "table"]
    if not tables:
        return

    for caption in blocks:
        if caption.type != "caption":
            continue
        if caption.meta.get("caption_kind") != "table":
            continue
        target = _nearest_table(caption, tables)
        if target is None:
            continue
        caption.meta["target_order"] = target.order
        target.meta["caption"] = caption.text
        if target.table is not None:
            label = caption.meta.get("label")
            if isinstance(label, str):
                target.meta.setdefault("label", label)


def _nearest_table(caption: Block, tables: list[Block]) -> Block | None:
    if caption.bbox is None:
        # Without geometry (DOCX) the nearest table in document order wins.
        after = [t for t in tables if t.order > caption.order]
        before = [t for t in tables if t.order < caption.order]
        if after and (not before or after[0].order - caption.order <= 1):
            return after[0]
        return before[-1] if before else None

    best: Block | None = None
    best_distance = _CAPTION_DISTANCE
    for table in tables:
        if table.page != caption.page or table.bbox is None:
            continue
        distance = min(
            abs(table.bbox.top - caption.bbox.bottom),
            abs(caption.bbox.top - table.bbox.bottom),
        )
        if distance < best_distance:
            best, best_distance = table, distance
    return best
