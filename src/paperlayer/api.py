"""The public entry point.

One function does the work. Format detection is by content, not by file
extension, so a PDF served with a ``.bin`` name still parses and an ``.docx``
that is really a ZIP of something else fails with a clear message instead of a
confusing one from deep inside a backend.
"""

from __future__ import annotations

import contextlib
import io
import os
import zipfile
from typing import Any, BinaryIO, Union

from .errors import UnsupportedFormatError
from .options import FootnoteMode, ParseOptions, TableMode
from .types import Document

__all__ = ["detect_format", "parse", "parse_bytes"]

Source = Union[str, "os.PathLike[str]", bytes, BinaryIO]

#: How far into a file to look for a signature. Some producers prepend junk
#: before ``%PDF``; the specification allows it and real files do it.
_SNIFF_BYTES = 2048

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
#: The part every DOCX has and no other OOXML format does.
_DOCX_MARKER = "word/document.xml"


def parse(
    source: Source,
    *,
    keep_headers: bool | None = None,
    table_mode: TableMode | None = None,
    footnote_mode: FootnoteMode | None = None,
    pages: tuple[int, int] | None = None,
    password: str | None = None,
    options: ParseOptions | None = None,
    **overrides: Any,
) -> Document:
    """Convert a PDF or DOCX into structured Markdown.

    ``source`` may be a path, raw bytes, or an open binary file object.

    Args:
        keep_headers: Keep running headers, footers and page numbers instead of
            stripping them. They are tagged ``meta["artifact"]`` when kept.
        table_mode: ``markdown`` (default), ``html``, ``csv``, ``text`` or
            ``drop``. Only ``html`` can express merged cells.
        footnote_mode: ``inline`` (default) keeps each footnote next to the
            block that references it, which is what you want for retrieval;
            ``end`` collects them at the bottom; ``drop`` removes them.
        pages: 1-based inclusive page range, PDF only.
        password: Password for an encrypted PDF.
        options: A fully built :class:`~paperlayer.ParseOptions`. Any explicit
            keyword argument still wins over the matching field.
        **overrides: Any other :class:`~paperlayer.ParseOptions` field.

    Returns:
        A :class:`~paperlayer.Document` exposing ``.markdown`` and ``.blocks``.

    Raises:
        UnsupportedFormatError: The input is neither a PDF nor a DOCX.
        ParseError: The file is malformed beyond recovery.
        PasswordRequiredError: The PDF is encrypted and the password failed.

    Example:
        >>> from paperlayer import parse
        >>> doc = parse("report.pdf")
        >>> doc.markdown[:40]
        ...
    """
    opts = options.replace() if options is not None else ParseOptions()
    if keep_headers is not None:
        opts.keep_headers = keep_headers
    if table_mode is not None:
        opts.table_mode = table_mode
    if footnote_mode is not None:
        opts.footnote_mode = footnote_mode
    if pages is not None:
        opts.pages = pages
    if password is not None:
        opts.password = password
    if overrides:
        opts = opts.replace(**overrides)
    opts.validate()

    fmt = detect_format(source)
    _rewind(source)

    if fmt == "pdf":
        return _parse_pdf(source, opts)
    return _parse_docx(source, opts)


def parse_bytes(
    data: bytes,
    *,
    format: str | None = None,
    **kwargs: Any,
) -> Document:
    """Parse a document already held in memory.

    ``format`` forces ``"pdf"`` or ``"docx"`` instead of sniffing the content.
    """
    if format is None:
        return parse(data, **kwargs)
    normalized = format.lower().lstrip(".")
    if normalized not in ("pdf", "docx"):
        raise UnsupportedFormatError(f"format must be 'pdf' or 'docx', got {format!r}")
    options = kwargs.pop("options", None)
    opts = options.replace() if options is not None else ParseOptions()
    if kwargs:
        opts = opts.replace(**{k: v for k, v in kwargs.items() if v is not None})
    opts.validate()
    return _parse_pdf(data, opts) if normalized == "pdf" else _parse_docx(data, opts)


def _parse_pdf(source: Source, options: ParseOptions) -> Document:
    from .pipeline import build_blocks
    from .readers.pdf import read_pdf

    raw, edges = read_pdf(source, options)
    blocks = build_blocks(raw, edges, options)
    return Document(
        blocks=blocks,
        source=raw.source,
        warnings=raw.warnings,
        options=options,
    )


def _parse_docx(source: Source, options: ParseOptions) -> Document:
    from .readers.docx import read_docx

    blocks, info, warnings = read_docx(source, options)
    return Document(blocks=blocks, source=info, warnings=warnings, options=options)


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------


def detect_format(source: Source) -> str:
    """Return ``"pdf"`` or ``"docx"``, by content rather than by extension."""
    head, full = _read_head(source)

    if _PDF_MAGIC in head:
        return "pdf"

    if head.startswith(_ZIP_MAGIC):
        if _is_docx(source, full):
            return "docx"
        raise UnsupportedFormatError(
            "The input is a ZIP archive but not a DOCX: it has no "
            f"{_DOCX_MARKER} part. XLSX and PPTX are not supported."
        )

    suffix = _suffix(source)
    if suffix in (".pdf", ".docx"):
        raise UnsupportedFormatError(
            f"The file is named {suffix} but its contents are not a valid "
            f"{suffix.lstrip('.').upper()} file (no signature found)."
        )
    raise UnsupportedFormatError(
        f"Unsupported input: expected a PDF or DOCX. The content starts with {head[:8]!r}."
    )


def _read_head(source: Source) -> tuple[bytes, bytes | None]:
    """First bytes of the source, plus the whole thing when already in memory."""
    if isinstance(source, bytes):
        return source[:_SNIFF_BYTES], source
    if isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, "rb") as handle:
            return handle.read(_SNIFF_BYTES), None
    if hasattr(source, "read"):
        stream: BinaryIO = source
        position = stream.tell() if hasattr(stream, "seek") else None
        head = stream.read(_SNIFF_BYTES)
        if position is not None:
            stream.seek(position)
        elif head:
            raise UnsupportedFormatError(
                "A non-seekable stream cannot be sniffed; read it into bytes "
                "first, or call parse_bytes(data, format=...)."
            )
        return head or b"", None
    raise UnsupportedFormatError(
        f"Cannot read a document from {type(source).__name__}; pass a path, "
        "bytes, or an open binary file."
    )


def _is_docx(source: Source, full: bytes | None) -> bool:
    try:
        if full is not None:
            with zipfile.ZipFile(io.BytesIO(full)) as archive:
                return _DOCX_MARKER in archive.namelist()
        if isinstance(source, (str, os.PathLike)):
            with zipfile.ZipFile(os.fspath(source)) as archive:
                return _DOCX_MARKER in archive.namelist()
        if hasattr(source, "read") and hasattr(source, "seek"):
            stream: BinaryIO = source  # type: ignore[assignment]
            position = stream.tell()
            try:
                with zipfile.ZipFile(stream) as archive:
                    return _DOCX_MARKER in archive.namelist()
            finally:
                stream.seek(position)
    except (zipfile.BadZipFile, OSError):
        return False
    return False


def _suffix(source: Source) -> str:
    if isinstance(source, (str, os.PathLike)):
        return os.path.splitext(os.fspath(source))[1].lower()
    name = getattr(source, "name", None)
    if isinstance(name, str):
        return os.path.splitext(name)[1].lower()
    return ""


def _rewind(source: Source) -> None:
    if hasattr(source, "seek"):
        # An unseekable stream has already been consumed by sniffing; the
        # reader will report that far more clearly than a rewind failure.
        with contextlib.suppress(OSError, ValueError):
            source.seek(0)
