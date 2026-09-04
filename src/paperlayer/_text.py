"""Text normalisation primitives shared by both readers.

PDF text extraction produces ligatures, soft hyphens, zero-width joiners and
words broken across lines. None of that survives into the Markdown, and all of
it is handled here rather than in the readers, so PDF and DOCX get identical
treatment.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "base_font_name",
    "collapse_ws",
    "escape_markdown",
    "escape_table_cell",
    "expand_ligatures",
    "heading_number_depth",
    "is_bold_font",
    "is_italic_font",
    "is_sentence_like",
    "join_hyphenated",
    "looks_like_page_number",
    "normalize_for_repeat",
    "normalize_superscript",
    "normalize_unicode",
    "split_list_marker",
]

# --------------------------------------------------------------------------
# Unicode normalisation
# --------------------------------------------------------------------------

#: Typographic ligatures. NFKC would also expand these, but it mangles far too
#: much else (superscript digits, fractions, non-breaking spaces inside numbers),
#: so we expand exactly the characters we mean to expand.
_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
    "Ĳ": "IJ",
    "ĳ": "ij",
    "Œ": "OE",
    "œ": "oe",
    "Æ": "AE",
    "æ": "ae",
}

#: Characters that carry no meaning once the layout is gone.
_ZERO_WIDTH = {
    "­": "",  # soft hyphen
    "​": "",  # zero-width space
    "‌": "",  # zero-width non-joiner
    "‍": "",  # zero-width joiner
    "﻿": "",  # BOM / zero-width no-break space
    "⁠": "",  # word joiner
}

#: Spacing and punctuation variants folded to their ASCII equivalent. Curly
#: quotes are deliberately preserved: they are valid Markdown and folding them
#: loses information for no token saving.
_SPACING = {
    " ": " ",  # no-break space
    " ": " ",  # figure space
    " ": " ",  # thin space
    " ": " ",  # narrow no-break space
    "　": " ",  # ideographic space
    "\t": " ",
    " ": "\n",  # line separator
    " ": "\n",  # paragraph separator
}

_TRANSLATE_FULL = str.maketrans({**_LIGATURES, **_ZERO_WIDTH, **_SPACING})
_TRANSLATE_LIGATURES = str.maketrans(_LIGATURES)

_SUPERSCRIPT_MAP = str.maketrans(
    {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
)

_WS_RE = re.compile(r"[  ]{2,}")


def expand_ligatures(text: str) -> str:
    """Replace typographic ligatures with their component letters."""
    return text.translate(_TRANSLATE_LIGATURES)


def normalize_unicode(text: str) -> str:
    """Expand ligatures, fold exotic spaces, drop zero-width marks.

    Combining marks are recomposed with NFC so that ``e`` + combining acute
    compares equal to a precomposed ``e``-acute, which matters when detecting
    running headers by string equality.
    """
    text = text.translate(_TRANSLATE_FULL)
    return unicodedata.normalize("NFC", text)


def normalize_superscript(text: str) -> str:
    """Map superscript digit glyphs to ordinary digits."""
    return text.translate(_SUPERSCRIPT_MAP)


def collapse_ws(text: str) -> str:
    """Collapse runs of spaces and trim. Newlines are preserved."""
    return _WS_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Dehyphenation
# --------------------------------------------------------------------------

_HYPHENS = ("-", "‐", "‑")
_WORD_TAIL_RE = re.compile(r"([A-Za-zÀ-ɏ]{2,})[-‐‑]$")
_WORD_HEAD_RE = re.compile(r"^([a-zß-ɏ]+)")


def join_hyphenated(prev: str, nxt: str) -> str | None:
    """Join two lines split by a trailing hyphen, or return ``None``.

    Only joins when the hyphen is doing line-breaking work: an alphabetic tail
    on the left, a lowercase alphabetic head on the right. ``self-`` followed by
    ``Employed`` keeps its hyphen, because a capital on the right side means a
    real compound rather than a break.
    """
    left = prev.rstrip()
    right = nxt.lstrip()
    if not left or not right:
        return None
    if not left.endswith(_HYPHENS):
        return None
    if not _WORD_TAIL_RE.search(left):
        return None
    if not _WORD_HEAD_RE.match(right):
        return None
    return left[:-1] + right


# --------------------------------------------------------------------------
# Markdown escaping
# --------------------------------------------------------------------------

#: Line-leading sequences that would turn a paragraph into some other block.
_LEADING_BLOCK_RE = re.compile(r"^(\s*)([#>+*-]|\d{1,3}[.)])(\s)")
#: A line of only dashes or equals becomes a horizontal rule or a setext heading.
_RULE_RE = re.compile(r"^\s*([-=_*])\1{2,}\s*$")
_BACKTICK_RE = re.compile(r"`")


def escape_markdown(text: str) -> str:
    """Escape only what would change the *structure* of the output.

    Deliberately conservative. Escaping every ``*`` and ``_`` inside prose
    inflates token counts and hurts readability, and stray emphasis markers do
    not break a retrieval pipeline. Escaping a leading ``#`` does matter,
    because it would fabricate a heading that is not in the document.
    """
    out: list[str] = []
    for line in text.split("\n"):
        line = _BACKTICK_RE.sub(r"\\`", line)
        if _RULE_RE.match(line):
            line = "\\" + line.lstrip()[0] + line.lstrip()[1:]
        else:
            line = _LEADING_BLOCK_RE.sub(r"\1\\\2\3", line)
        out.append(line)
    return "\n".join(out)


def escape_table_cell(text: str) -> str:
    """Make a cell safe inside a pipe table: no bare pipes, no newlines."""
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = re.sub(r"\s*\n\s*", "<br>", text.strip())
    return collapse_ws(text)


# --------------------------------------------------------------------------
# Font name interpretation
# --------------------------------------------------------------------------

#: pdfminer reports subset fonts as ``ABCDEF+Helvetica-Bold``.
_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_BOLD_TOKENS = ("bold", "black", "heavy", "semib", "demib", "extrab", "ultrab")
_ITALIC_TOKENS = ("italic", "oblique")
_BOLD_SUFFIX_RE = re.compile(r"[,\-_ ](b|bd|bold)$")
_ITALIC_SUFFIX_RE = re.compile(r"[,\-_ ](i|it|ital)$")


def base_font_name(name: str | None) -> str:
    """Strip the subset prefix from a font name."""
    if not name:
        return ""
    return _SUBSET_RE.sub("", name)


def is_bold_font(name: str | None) -> bool:
    """Whether a PDF font name denotes a bold weight."""
    n = base_font_name(name).lower()
    if not n:
        return False
    if any(token in n for token in _BOLD_TOKENS):
        return True
    return bool(_BOLD_SUFFIX_RE.search(n))


def is_italic_font(name: str | None) -> bool:
    """Whether a PDF font name denotes an italic or oblique style."""
    n = base_font_name(name).lower()
    if not n:
        return False
    if any(token in n for token in _ITALIC_TOKENS):
        return True
    return bool(_ITALIC_SUFFIX_RE.search(n))


# --------------------------------------------------------------------------
# Running header / footer signatures
# --------------------------------------------------------------------------

_DIGITS_RE = re.compile(r"\d+")
_PUNCT_EDGE_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)

_PAGE_NUMBER_PATTERNS = (
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^[-–—|]\s*\d{1,4}\s*[-–—|]$"),
    re.compile(r"^\[\s*\d{1,4}\s*\]$"),
    re.compile(r"^(page|seite|p\.?|pg\.?)\s*\d{1,4}$", re.IGNORECASE),
    re.compile(
        r"^(page|seite|p\.?|pg\.?)?\s*\d{1,4}\s*(of|/|von|\|)\s*\d{1,4}$",
        re.IGNORECASE,
    ),
    re.compile(r"^[ivxlcdm]{1,7}$", re.IGNORECASE),
    re.compile(r"^[A-Za-z]{1,2}[-.–]\d{1,4}$"),
)


def normalize_for_repeat(text: str) -> str:
    """A signature for cross-page comparison.

    Digits collapse to ``#`` so that ``Page 3 of 40`` and ``Page 4 of 40``
    share a signature; case and edge punctuation are dropped so that a header
    reset in small caps on one page still matches.
    """
    sig = collapse_ws(text).lower()
    sig = _DIGITS_RE.sub("#", sig)
    sig = _PUNCT_EDGE_RE.sub("", sig)
    return sig


def looks_like_page_number(text: str) -> bool:
    """Whether a short line is a bare page number in any common dressing."""
    stripped = collapse_ws(text)
    if not stripped or len(stripped) > 24:
        return False
    return any(p.match(stripped) for p in _PAGE_NUMBER_PATTERNS)


# --------------------------------------------------------------------------
# List markers
# --------------------------------------------------------------------------

#: Glyphs used as bullets. ``-`` is included but handled carefully: a hyphen
#: only counts as a bullet when followed by whitespace.
BULLET_CHARS = "•●○▪■‣⁃∙·‐–—-*◦➢"

_BULLET_RE = re.compile(rf"^([{re.escape(BULLET_CHARS)}])\s+(.*)$", re.DOTALL)
_ORDERED_RE = re.compile(
    r"^\(?((?:\d{1,3})|(?:[ivxlcdm]{1,7})|(?:[A-Za-z]))[.)\]]\s+(.*)$",
    re.DOTALL,
)


def split_list_marker(text: str) -> tuple[str, str, bool] | None:
    """Split a leading list marker off a line.

    Returns ``(marker, remainder, ordered)`` or ``None`` when the line does not
    start a list item. An ordered marker must be followed by real content, which
    is what keeps a sentence like ``1. e.g.`` from swallowing the whole line.
    """
    stripped = text.lstrip()
    m = _BULLET_RE.match(stripped)
    if m:
        rest = m.group(2).strip()
        return (m.group(1), rest, False) if rest else None
    m = _ORDERED_RE.match(stripped)
    if m:
        rest = m.group(2).strip()
        if not rest:
            return None
        # A single letter marker is ambiguous with an initial ("J. Smith"),
        # so require the remainder to start like a sentence, not a surname.
        marker = m.group(1)
        if (
            len(marker) == 1
            and marker.isalpha()
            and rest[:1].isupper()
            and len(rest.split()) <= 3
        ):
            return None
        return marker, rest, True
    return None


# --------------------------------------------------------------------------
# Heading shape heuristics
# --------------------------------------------------------------------------

_HEADING_NUMBER_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")
_SENTENCE_END_RE = re.compile(r"[.!?]['\"”’)\]]?$")


def heading_number_depth(text: str) -> int | None:
    """Depth implied by a section number: ``3.2.1 Scope`` gives 3.

    Returns ``None`` when the line does not open with a section number.
    """
    m = _HEADING_NUMBER_RE.match(text.strip())
    if not m:
        return None
    return m.group(1).count(".") + 1


def is_sentence_like(text: str) -> bool:
    """Whether a line reads as running prose rather than a title.

    Used to veto heading candidates: a big bold line is still a heading, but a
    big bold line containing three sentences is a pull quote.
    """
    stripped = text.strip()
    if not stripped:
        return False
    # A trailing period alone is weak evidence; combined with length or with a
    # second sentence it is decisive.
    if (
        _SENTENCE_END_RE.search(stripped)
        and not stripped.endswith("..")
        and (len(stripped) > 90 or re.search(r"[.!?]\s+[A-Z]", stripped))
    ):
        return True
    return len(stripped) > 220
