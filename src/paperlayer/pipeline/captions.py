"""Figure and table caption detection.

A caption is short, starts with a label word and a number, and sits next to the
thing it describes. Keeping it as its own block type matters for retrieval: a
caption is the highest-signal description of a table that a text index will
ever see, and merging it into the surrounding paragraph loses that.
"""

from __future__ import annotations

import re

__all__ = ["CAPTION_KINDS", "caption_label"]

#: Label words, English plus the German and French ones that show up most in
#: technical documents. Matching is case-insensitive.
CAPTION_KINDS: dict[str, str] = {
    "figure": "figure",
    "fig": "figure",
    "abbildung": "figure",
    "abb": "figure",
    "bild": "figure",
    "diagram": "figure",
    "chart": "figure",
    "graph": "figure",
    "image": "figure",
    "photo": "figure",
    "plate": "figure",
    "map": "figure",
    "table": "table",
    "tab": "table",
    "tabelle": "table",
    "tableau": "table",
    "exhibit": "table",
    "listing": "code",
    "algorithm": "code",
    "example": "code",
    "equation": "equation",
}

_CAPTION_RE = re.compile(
    r"^(?P<word>[A-Za-zÄÖÜäöü]{3,10})\.?\s*"
    r"(?P<number>\d{1,3}(?:[.\-]\d{1,3})*)\s*"
    r"(?P<sep>[:.\)–—-]|\s)\s*",
)

#: A caption longer than this is a paragraph that happens to start with a label.
_MAX_CAPTION_CHARS = 400


def caption_label(text: str) -> tuple[str, str] | None:
    """``(kind, label)`` when ``text`` opens like a caption, else ``None``.

    ``kind`` is one of the values in :data:`CAPTION_KINDS` -- ``figure``,
    ``table``, ``code`` or ``equation`` -- and ``label`` is the label as
    written, e.g. ``Table 3.1``.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_CAPTION_CHARS:
        return None
    match = _CAPTION_RE.match(stripped)
    if not match:
        return None
    word = match.group("word").lower().rstrip(".")
    kind = CAPTION_KINDS.get(word)
    if kind is None:
        return None
    # "Table 4" alone is a cross-reference inside a sentence unless something
    # follows it; a caption always has a body.
    if not stripped[match.end() :].strip():
        return None
    label = f"{match.group('word').rstrip('.')} {match.group('number')}"
    return kind, label
