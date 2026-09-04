"""Blocks to Markdown.

Rendering is deliberately plain. Inline emphasis from the source is dropped,
because bold and italic runs cost tokens in an embedding pipeline and carry
almost no retrieval signal, while the structure that does carry signal
(headings, lists, tables, footnote attachment) is preserved exactly.
"""

from __future__ import annotations

import csv
import html
import io
import re

from .._text import escape_markdown, escape_table_cell
from ..options import ParseOptions
from ..types import Block, Document, Footnote, TableData

__all__ = ["render_block", "render_document", "render_table"]

_REF_RE = re.compile(r"\[\^[^\]]{1,3}\]")


def render_document(doc: Document, options: ParseOptions) -> str:
    """Render a whole document, honouring the footnote mode."""
    chunks: list[str] = []
    for block in doc.blocks:
        rendered = render_block(block, options)
        if rendered:
            chunks.append(rendered)

    if options.footnote_mode == "end":
        notes = [note for block in doc.blocks for note in block.footnotes]
        if notes:
            chunks.append(_render_notes(notes))

    return "\n\n".join(chunks).strip() + "\n" if chunks else ""


def render_block(block: Block, options: ParseOptions) -> str:
    """Render one block, including its footnotes when the mode says so."""
    body = _render_body(block, options)
    if not body:
        return ""

    if options.footnote_mode == "drop":
        body = _REF_RE.sub("", body)
        return body.strip()

    if options.footnote_mode == "inline" and block.footnotes:
        body = f"{body}\n\n{_render_notes(block.footnotes)}"
    return body


def _render_body(block: Block, options: ParseOptions) -> str:
    if block.type == "heading":
        level = min(max(block.level or 1, 1), 6)
        text = _one_line(block.text)
        return f"{'#' * level} {text}" if text else ""

    if block.type == "table":
        if options.table_mode == "drop":
            return ""
        if block.table is None:
            return _escape(block.text, options)
        return render_table(block.table, options)

    if block.type == "list":
        return _render_list(block, options)

    if block.type == "caption":
        text = _one_line(_escape(block.text, options))
        return f"*{text}*" if text else ""

    if block.type == "code":
        return f"```\n{block.text.rstrip()}\n```"

    if block.type == "footnote":
        return _render_notes(block.footnotes) if block.footnotes else ""

    return _escape(block.text, options)


def _escape(text: str, options: ParseOptions) -> str:
    return escape_markdown(text) if options.escape_markdown else text


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _render_list(block: Block, options: ParseOptions) -> str:
    items = block.meta.get("items")
    if not isinstance(items, list) or not items:
        return _escape(block.text, options)

    lines: list[str] = []
    # Ordered items are renumbered per nesting level, so a list broken across a
    # page boundary still reads 1, 2, 3 rather than restarting.
    counters: dict[int, int] = {}
    for entry in items:
        level = int(entry.get("level", 0))
        text = _one_line(_escape(str(entry.get("text", "")), options))
        if not text:
            continue
        if entry.get("ordered"):
            counters[level] = counters.get(level, 0) + 1
            for deeper in [k for k in counters if k > level]:
                del counters[deeper]
            bullet = f"{counters[level]}."
        else:
            bullet = "-"
        lines.append(f"{'  ' * level}{bullet} {text}")
    return "\n".join(lines)


def render_table(table: TableData, options: ParseOptions) -> str:
    """Render a table in the configured mode."""
    rows = table.all_rows()
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]

    mode = options.table_mode
    if mode == "html":
        return _render_html(padded, table.header is not None)
    if mode == "csv":
        return _render_csv(padded)
    if mode == "text":
        return "\n".join("\t".join(_one_line(c) for c in row) for row in padded)
    return _render_pipe(padded, table.header is not None)


def _render_pipe(rows: list[list[str]], has_header: bool) -> str:
    """GitHub-flavoured pipe table.

    A pipe table has no way to express "no header", so when detection found
    none the first row is promoted. The truth stays available on the block:
    ``block.table.header is None`` means the promotion happened here.
    """
    cells = [[escape_table_cell(c) for c in row] for row in rows]
    header = cells[0]
    body = cells[1:]
    if not has_header and len(cells) == 1:
        header, body = cells[0], []

    out = ["| " + " | ".join(header) + " |"]
    out.append("| " + " | ".join("---" for _ in header) + " |")
    for row in body:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _render_html(rows: list[list[str]], has_header: bool) -> str:
    out = ["<table>"]
    start = 0
    if has_header:
        cells = "".join(f"<th>{_cell_html(c)}</th>" for c in rows[0])
        out.append(f"<thead><tr>{cells}</tr></thead>")
        start = 1
    out.append("<tbody>")
    for row in rows[start:]:
        out.append("<tr>" + "".join(f"<td>{_cell_html(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def _cell_html(cell: str) -> str:
    return html.escape(cell.strip()).replace("\n", "<br>")


def _render_csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([_one_line(c) for c in row])
    return "```csv\n" + buffer.getvalue().rstrip("\n") + "\n```"


def _render_notes(notes: list[Footnote]) -> str:
    return "\n".join(f"[^{note.marker}]: {_one_line(note.text)}" for note in notes)
