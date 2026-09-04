"""paperlayer: PDF and DOCX to clean, structured Markdown for RAG pipelines.

    >>> from paperlayer import parse
    >>> doc = parse("report.pdf")
    >>> doc.markdown          # one Markdown string
    >>> doc.blocks            # structured blocks with page, type and level

Fully local: no API keys, no network calls, no model downloads. The heavy
backends are imported only when a document of that format is actually parsed,
so ``import paperlayer`` stays fast.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .api import detect_format, parse, parse_bytes
from .errors import (
    MissingBackendError,
    PaperlayerError,
    ParseError,
    PasswordRequiredError,
    UnsupportedFormatError,
)
from .options import FootnoteMode, ParseOptions, TableMode
from .types import (
    BBox,
    Block,
    BlockType,
    Document,
    Footnote,
    SourceInfo,
    StyleInfo,
    TableData,
)

__all__ = [
    "BBox",
    "Block",
    "BlockType",
    "Document",
    "Footnote",
    "FootnoteMode",
    "MissingBackendError",
    "PaperlayerError",
    "ParseError",
    "ParseOptions",
    "PasswordRequiredError",
    "SourceInfo",
    "StyleInfo",
    "TableData",
    "TableMode",
    "UnsupportedFormatError",
    "__version__",
    "detect_format",
    "parse",
    "parse_bytes",
]
