"""A tiny DOCX writer, used to build test fixtures.

A .docx is a ZIP of XML parts, so stdlib ``zipfile`` is all this needs. Writing
the parts by hand lets a test say exactly what it means -- this paragraph uses
the Heading 2 style, that one is 16pt bold with no style at all, this list is
numbered at level 1 -- which is the difference between testing the reader and
testing python-docx.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Sequence
from io import BytesIO

__all__ = ["build_docx", "paragraph", "table"]

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
</Relationships>"""


def _style(
    style_id: str,
    name: str,
    *,
    size: float | None = None,
    bold: bool = False,
    outline: int | None = None,
    based_on: str | None = None,
) -> str:
    parts = [
        f'<w:style w:type="paragraph" w:styleId="{style_id}">',
        f'<w:name w:val="{name}"/>',
    ]
    if based_on:
        parts.append(f'<w:basedOn w:val="{based_on}"/>')
    if outline is not None:
        parts.append(f'<w:pPr><w:outlineLvl w:val="{outline}"/></w:pPr>')
    rpr = ""
    if bold:
        rpr += "<w:b/>"
    if size is not None:
        rpr += f'<w:sz w:val="{int(size * 2)}"/>'
    if rpr:
        parts.append(f"<w:rPr>{rpr}</w:rPr>")
    parts.append("</w:style>")
    return "".join(parts)


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {_W}>"
    '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    + _style("Normal", "Normal", size=11)
    + _style("Heading1", "heading 1", size=16, bold=True, outline=0, based_on="Normal")
    + _style("Heading2", "heading 2", size=13, bold=True, outline=1, based_on="Normal")
    + _style("Heading3", "heading 3", size=12, bold=True, outline=2, based_on="Normal")
    + _style("Title", "Title", size=22, bold=True, based_on="Normal")
    + _style("Caption", "caption", size=9, based_on="Normal")
    + _style("ListParagraph", "List Paragraph", based_on="Normal")
    + "</w:styles>"
)


def _numbering() -> str:
    levels = "".join(
        f'<w:lvl w:ilvl="{i}"><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl>'
        for i in range(3)
    )
    ordered = "".join(
        f'<w:lvl w:ilvl="{i}"><w:numFmt w:val="decimal"/><w:lvlText w:val="%{i + 1}."/></w:lvl>'
        for i in range(3)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:numbering {_W}>"
        f'<w:abstractNum w:abstractNumId="0">{levels}</w:abstractNum>'
        f'<w:abstractNum w:abstractNumId="1">{ordered}</w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
        "</w:numbering>"
    )


def _footnotes(notes: dict[int, str]) -> str:
    body = '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t></w:t></w:r></w:p></w:footnote>'
    for note_id, text in sorted(notes.items()):
        body += (
            f'<w:footnote w:id="{note_id}"><w:p><w:r><w:t>{_escape(text)}</w:t>'
            "</w:r></w:p></w:footnote>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:footnotes {_W}>{body}</w:footnotes>"
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paragraph(
    text: str,
    *,
    style: str | None = None,
    size: float | None = None,
    bold: bool = False,
    num_id: int | None = None,
    level: int = 0,
    footnote: int | None = None,
) -> str:
    """One ``w:p``. ``footnote`` appends a reference to that footnote id."""
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num_id is not None:
        ppr += f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
    ppr = f"<w:pPr>{ppr}</w:pPr>" if ppr else ""

    rpr = ""
    if bold:
        rpr += "<w:b/>"
    if size is not None:
        rpr += f'<w:sz w:val="{int(size * 2)}"/>'
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""

    run = f'<w:r>{rpr}<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
    if footnote is not None:
        run += f'<w:r>{rpr}<w:footnoteReference w:id="{footnote}"/></w:r>'
    return f"<w:p>{ppr}{run}</w:p>"


def table(
    rows: Sequence[Sequence[str]], *, header: bool = False, bold_first_row: bool = False
) -> str:
    """One ``w:tbl`` from a grid of strings."""
    out = ["<w:tbl>"]
    for index, row in enumerate(rows):
        trpr = "<w:trPr><w:tblHeader/></w:trPr>" if header and index == 0 else ""
        cells = []
        for cell in row:
            rpr = "<w:rPr><w:b/></w:rPr>" if bold_first_row and index == 0 else ""
            cells.append(
                "<w:tc><w:p><w:r>"
                f'{rpr}<w:t xml:space="preserve">{_escape(cell)}</w:t>'
                "</w:r></w:p></w:tc>"
            )
        out.append(f"<w:tr>{trpr}{''.join(cells)}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def build_docx(
    body: Iterable[str],
    *,
    notes: dict[int, str] | None = None,
) -> bytes:
    """Assemble body XML fragments into a complete .docx file."""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_W}><w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        "</w:body></w:document>"
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/numbering.xml", _numbering())
        archive.writestr("word/footnotes.xml", _footnotes(notes or {}))
    return buffer.getvalue()
