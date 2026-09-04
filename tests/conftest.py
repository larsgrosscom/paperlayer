"""Shared fixtures.

Every fixture is generated in-process by :mod:`pdfgen` and :mod:`docxgen`, so
the repository carries no binary test data and each fixture states its own
typography explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from docxgen import build_docx, paragraph, table
from pdfgen import Page, build_pdf

BODY = 11.0


@pytest.fixture
def simple_report() -> bytes:
    """One page: a title, two paragraphs and a subheading."""
    page = Page()
    page.text(72, 100, "Quarterly Report", size=18, bold=True)
    page.text(72, 140, "This is the opening paragraph of the report and it", size=BODY)
    page.text(72, 154, "continues onto a second line before it ends.", size=BODY)
    page.text(72, 190, "Revenue Breakdown", size=13, bold=True)
    page.text(72, 215, "Revenue grew across every region during the period.", size=BODY)
    return build_pdf([page])


@pytest.fixture
def ruled_table_pdf() -> bytes:
    """One page with a fully ruled three-column table and a caption."""
    page = Page()
    page.text(72, 90, "Results", size=15, bold=True)
    xs = [72.0, 220.0, 360.0, 470.0]
    ys = [110.0, 130.0, 150.0, 170.0]
    page.grid(xs, ys)
    rows = [
        ["Region", "Revenue", "Growth"],
        ["EMEA", "1,204", "12%"],
        ["APAC", "980", "8%"],
    ]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.text(xs[c] + 5, ys[r] + 14, cell, size=10, bold=(r == 0))
    page.text(72, 200, "Table 1. Regional revenue for FY24.", size=9)
    return build_pdf([page])


@pytest.fixture
def paged_report() -> bytes:
    """Three pages with a running header, page numbers and one footnote each."""
    bodies = [
        "The first section discusses overall market conditions in some detail",
        "The second section reviews the competitive landscape and its drivers",
        "The third section sets out the recommendations that follow from this",
    ]
    pages = []
    for n in (1, 2, 3):
        page = Page()
        page.text(72, 40, "ACME Corporation - Confidential", size=8)
        page.text(72, 120, f"Section {n}", size=14, bold=True)
        page.text(72, 150, bodies[n - 1], size=BODY)
        page.text(430, 145.5, str(n), size=7)
        page.text(72, 164, f"and the analysis continues on page {n} below.", size=BODY)
        page.text(72, 700, f"{n}  Footnote {n} explaining the claim in more depth.", size=8)
        page.text(300, 760, str(n), size=9)
        pages.append(page)
    return build_pdf(pages)


@pytest.fixture
def two_column_pdf() -> bytes:
    """A banner heading over two columns of body text."""
    page = Page()
    page.text(60, 80, "A Two Column Paper With A Long Banner Title", size=17, bold=True)
    left = [
        "Alpha one line of the left column",
        "Alpha two continues the left",
        "Alpha three still on the left",
        "Alpha four keeps going here",
        "Alpha five is another line",
        "Alpha six ends the left side.",
    ]
    right = [
        "Beta one starts the right column",
        "Beta two continues the right",
        "Beta three still on the right",
        "Beta four keeps going here",
        "Beta five is another line",
        "Beta six ends the right side.",
    ]
    y = 130.0
    for a, b in zip(left, right, strict=True):
        page.text(60, y, a, size=10)
        page.text(330, y, b, size=10)
        y += 15
    return build_pdf([page])


@pytest.fixture
def list_pdf() -> bytes:
    """A bullet list with one nested item, then an ordered list."""
    page = Page()
    page.text(72, 90, "Requirements", size=14, bold=True)
    page.text(72, 120, "- The system must accept PDF and DOCX input", size=BODY)
    page.text(72, 136, "- It must run entirely offline", size=BODY)
    page.text(90, 152, "- including in air-gapped environments", size=BODY)
    page.text(72, 168, "- Output must be valid Markdown", size=BODY)
    page.text(72, 205, "Numbered steps follow below in order.", size=BODY)
    y = 240.0
    for i, text in enumerate(["Install the package", "Call parse", "Read the output"], 1):
        page.text(72, y, f"{i}. {text}", size=BODY)
        y += 16
    return build_pdf([page])


@pytest.fixture
def structured_docx() -> bytes:
    """A DOCX exercising styles, numbering, a table, a caption and a footnote."""
    body = [
        paragraph("Introduction", style="Heading1"),
        paragraph("This paragraph explains the background of the review.", footnote=2),
        paragraph("Scope", style="Heading2"),
        paragraph("First bullet point", num_id=1, level=0),
        paragraph("Nested bullet point", num_id=1, level=1),
        paragraph("Second bullet point", num_id=1, level=0),
        paragraph("Step one", num_id=2, level=0),
        paragraph("Step two", num_id=2, level=0),
        table(
            [["Region", "Revenue"], ["EMEA", "1,204"], ["APAC", "980"]],
            header=True,
            bold_first_row=True,
        ),
        paragraph("Table 1. Revenue by region.", style="Caption"),
    ]
    return build_docx(body, notes={2: "A clarifying footnote from Word."})


@pytest.fixture
def unstyled_docx() -> bytes:
    """A DOCX typed by hand: direct formatting only, no heading styles."""
    body = [
        paragraph("Project Overview", size=18, bold=True),
        paragraph(
            "This document was written without using any of the built-in "
            "heading styles, which is how a great many real files arrive."
        ),
        paragraph("Background", size=14, bold=True),
        paragraph("The background section explains where the project came from."),
    ]
    return build_docx(body)
