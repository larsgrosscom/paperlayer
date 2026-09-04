"""Command line interface: ``paperlayer file.pdf``.

Useful for eyeballing what the library does to a document before wiring it
into a pipeline, and for shell-level conversion.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .api import parse
from .errors import PaperlayerError

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperlayer",
        description="Convert PDF and DOCX files to clean, structured Markdown.",
    )
    parser.add_argument("path", help="PDF or DOCX file to convert")
    parser.add_argument(
        "-o",
        "--out",
        metavar="FILE",
        help="write to FILE instead of stdout",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit structured blocks as JSON instead of Markdown",
    )
    parser.add_argument(
        "--keep-headers",
        action="store_true",
        help="keep running headers, footers and page numbers",
    )
    parser.add_argument(
        "--table-mode",
        choices=("markdown", "html", "csv", "text", "drop"),
        default="markdown",
        help="how to render tables (default: markdown)",
    )
    parser.add_argument(
        "--footnote-mode",
        choices=("inline", "end", "drop"),
        default="inline",
        help="where footnote definitions go (default: inline)",
    )
    parser.add_argument(
        "--pages",
        metavar="FIRST-LAST",
        help="1-based inclusive page range, e.g. 1-10 (PDF only)",
    )
    parser.add_argument(
        "--password",
        help="password for an encrypted PDF",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="print a block-type summary to stderr",
    )
    parser.add_argument("--version", action="version", version=f"paperlayer {__version__}")
    return parser


def _page_range(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        first, _, last = value.partition("-")
        return (int(first), int(last or first))
    except ValueError as exc:
        raise SystemExit(f"paperlayer: invalid page range {value!r}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        doc = parse(
            args.path,
            keep_headers=args.keep_headers,
            table_mode=args.table_mode,
            footnote_mode=args.footnote_mode,
            pages=_page_range(args.pages),
            password=args.password,
        )
    except PaperlayerError as exc:
        print(f"paperlayer: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"paperlayer: no such file: {args.path}", file=sys.stderr)
        return 1

    output = doc.to_json() if args.json else doc.markdown

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output)
    else:
        sys.stdout.write(output)

    for warning in doc.warnings:
        print(f"paperlayer: {warning}", file=sys.stderr)

    if args.stats:
        counts: dict[str, int] = {}
        for block in doc.blocks:
            counts[block.type] = counts.get(block.type, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"paperlayer: {len(doc.blocks)} blocks ({summary})", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
