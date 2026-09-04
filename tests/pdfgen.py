"""A tiny PDF writer, used to build test fixtures.

Written by hand rather than pulled from reportlab for two reasons: the test
suite then has no dependency beyond the library itself, and fixtures stay
byte-for-byte deterministic, so a test that asserts "this line is 14pt bold at
y=72" is asserting about something exact rather than about whatever a layout
engine decided.

Only the subset paperlayer cares about is implemented: positioned text in the
base-14 fonts, and vector rules for table borders.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Page", "build_pdf", "simple_pdf"]

#: Base-14 fonts need no embedding and pdfminer knows their metrics.
_FONTS = {
    (False, False): ("F1", "Helvetica"),
    (True, False): ("F2", "Helvetica-Bold"),
    (False, True): ("F3", "Helvetica-Oblique"),
    (True, True): ("F4", "Helvetica-BoldOblique"),
}

LETTER = (612.0, 792.0)


@dataclass
class Page:
    """One page under construction. ``y`` is measured from the top."""

    width: float = LETTER[0]
    height: float = LETTER[1]
    _ops: list[str] = field(default_factory=list)

    def text(
        self,
        x: float,
        y: float,
        content: str,
        *,
        size: float = 11.0,
        bold: bool = False,
        italic: bool = False,
    ) -> Page:
        """Draw a string with its baseline at ``y`` points from the top."""
        name, _ = _FONTS[(bold, italic)]
        baseline = self.height - y
        self._ops.append(
            f"BT /{name} {size:g} Tf 1 0 0 1 {x:g} {baseline:g} Tm ({_escape(content)}) Tj ET"
        )
        return self

    def line(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.7) -> Page:
        """Draw a straight rule, coordinates measured from the top."""
        self._ops.append(
            f"{width:g} w {x0:g} {self.height - y0:g} m {x1:g} {self.height - y1:g} l S"
        )
        return self

    def grid(
        self,
        x_positions: list[float],
        y_positions: list[float],
        *,
        width: float = 0.7,
    ) -> Page:
        """Draw a full table grid: every vertical and horizontal rule."""
        for x in x_positions:
            self.line(x, y_positions[0], x, y_positions[-1], width=width)
        for y in y_positions:
            self.line(x_positions[0], y, x_positions[-1], y, width=width)
        return self

    def content(self) -> str:
        return "\n".join(self._ops)


def _escape(text: str) -> str:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    # Helvetica is declared with WinAnsiEncoding; anything outside it would
    # render as a wrong glyph, so fixtures stay inside that range.
    return out


def build_pdf(pages: list[Page]) -> bytes:
    """Serialise pages into a complete, valid PDF file."""
    if not pages:
        raise ValueError("a PDF needs at least one page")

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # Reserve 1 for the catalog and 2 for the page tree so the page objects can
    # reference their parent before it exists.
    add(b"")  # 1: catalog
    add(b"")  # 2: pages

    font_ids: dict[str, int] = {}
    for name, base in _FONTS.values():
        font_ids[name] = add(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
            f"/Encoding /WinAnsiEncoding >>".encode("latin-1")
        )

    resources = (
        "<< /Font << "
        + " ".join(f"/{name} {oid} 0 R" for name, oid in font_ids.items())
        + " >> >>"
    )

    page_ids: list[int] = []
    for page in pages:
        stream = page.content().encode("latin-1", "replace")
        content_id = add(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        page_id = add(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {page.width:g} {page.height:g}] "
                f"/Resources {resources} /Contents {content_id} 0 R >>"
            ).encode("latin-1")
        )
        page_ids.append(page_id)

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = (f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>").encode("latin-1")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def simple_pdf(lines: list[tuple[float, float, str, float, bool]]) -> bytes:
    """One page from ``(x, y, text, size, bold)`` tuples. For short tests."""
    page = Page()
    for x, y, content, size, bold in lines:
        page.text(x, y, content, size=size, bold=bold)
    return build_pdf([page])
