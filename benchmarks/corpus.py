"""Generates a synthetic document corpus, so the benchmark runs with no data.

The documents deliberately contain the things that separate a structured
extractor from a naive one: running headers, page numbers, ruled tables,
footnotes, two-column layouts and hyphenated line breaks. Point the benchmark
at your own folder for numbers that reflect your corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from docxgen import build_docx, paragraph, table
from pdfgen import Page, build_pdf

__all__ = ["write_corpus"]

_LOREM = [
    "The programme delivered measurable improvements across every operating",
    "region during the reporting period, with particular strength in services",
    "revenue. Management attributes the result to disciplined cost control and",
    "to the completion of the platform migration begun in the prior year.",
    "Looking ahead, the board expects the rate of growth to moderate as the",
    "comparison base normalises, while margins should continue to expand.",
]


def _report_pdf(pages: int = 6) -> bytes:
    """A conventional business report: headers, footers, tables, footnotes."""
    out = []
    for n in range(1, pages + 1):
        page = Page()
        page.text(72, 42, "Northwind Industries - Annual Report 2024", size=8)
        page.text(72, 96, f"{n}. Operating Review", size=15, bold=True)

        y = 130.0
        # Rotate the prose per page: real documents do not repeat body text
        # verbatim, and a corpus that did would flatter the artifact stage.
        rotated = _LOREM[(n - 1) % len(_LOREM) :] + _LOREM[: (n - 1) % len(_LOREM)]
        for i, line in enumerate(rotated):
            page.text(72, y, line, size=10.5)
            y += 14
            if i == 2:
                page.text(470, y - 17.5, "1", size=6.5)

        page.text(72, y + 26, f"{n}.1 Segment Detail", size=12, bold=True)

        xs = [72.0, 240.0, 360.0, 470.0]
        top = y + 46
        ys = [top, top + 20, top + 20 * 2, top + 20 * 3, top + 20 * 4]
        page.grid(xs, ys)
        rows = [
            ["Segment", "Revenue", "Margin"],
            ["Industrial", f"{1200 + n * 13:,}", "18.4%"],
            ["Consumer", f"{860 + n * 9:,}", "12.1%"],
            ["Services", f"{540 + n * 7:,}", "24.9%"],
        ]
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                page.text(xs[c] + 6, ys[r] + 14, cell, size=9.5, bold=(r == 0))

        page.text(72, ys[-1] + 40, f"Table {n}. Segment results for period {n}.", size=8.5)
        page.text(72, ys[-1] + 70, "Costs were held broadly flat despite infla-", size=10.5)
        page.text(72, ys[-1] + 84, "tionary pressure in the supply chain.", size=10.5)

        page.text(72, 706, "1  Excludes one-off restructuring charges.", size=7.5)
        page.text(300, 752, f"Page {n} of {pages}", size=8)
        out.append(page)
    return build_pdf(out)


def _paper_pdf(pages: int = 4) -> bytes:
    """A two-column academic paper, the hardest layout for naive extraction."""
    out = []
    left = [
        "We evaluate the approach on three public",
        "benchmarks and observe consistent gains",
        "over the strongest published baseline. The",
        "improvement is largest on the long-document",
        "split, where context fragmentation dominates",
        "the error profile of prior systems.",
        "Ablations isolate the contribution of each",
        "component of the pipeline in turn.",
    ]
    right = [
        "Recovering reading order first is what makes",
        "the remaining stages tractable, since every",
        "later decision is conditioned on a coherent",
        "linear sequence of text. Removing it costs",
        "roughly nine points of exact match, more",
        "than any other single ablation we ran.",
        "We release the evaluation harness alongside",
        "the trained checkpoints.",
    ]
    for n in range(1, pages + 1):
        page = Page()
        page.text(60, 40, "Preprint - under review", size=7.5)
        if n == 1:
            page.text(60, 92, "Layout Aware Document Parsing", size=17, bold=True)
            page.text(60, 118, "For Retrieval Augmented Generation", size=17, bold=True)
            start = 160.0
        else:
            start = 96.0
        page.text(60, start, f"{n}. Experiments", size=12, bold=True)
        y = start + 26
        for a, b in zip(left, right, strict=True):
            page.text(60, y, a, size=9.5)
            page.text(320, y, b, size=9.5)
            y += 13
        page.text(300, 756, str(n), size=8)
        out.append(page)
    return build_pdf(out)


def _memo_docx() -> bytes:
    body = [
        paragraph("Internal Memorandum", style="Title"),
        paragraph("Purpose", style="Heading1"),
        paragraph(
            "This memorandum sets out the proposed change to the retention "
            "policy and the reasoning behind it.",
            footnote=2,
        ),
        paragraph("Scope", style="Heading2"),
        paragraph("Applies to all production data stores", num_id=1),
        paragraph("Excludes anonymised analytics extracts", num_id=1),
        paragraph("Takes effect from the next quarter", num_id=1),
        paragraph("Actions", style="Heading2"),
        paragraph("Draft the updated policy text", num_id=2),
        paragraph("Circulate for legal review", num_id=2),
        paragraph("Publish to the internal handbook", num_id=2),
        table(
            [
                ["Store", "Current", "Proposed"],
                ["Orders", "7 years", "5 years"],
                ["Sessions", "18 months", "6 months"],
                ["Audit log", "10 years", "10 years"],
            ],
            header=True,
            bold_first_row=True,
        ),
        paragraph("Table 1. Retention periods by store.", style="Caption"),
    ]
    return build_docx(body, notes={2: "Legal reviewed an earlier draft in March."})


def write_corpus(directory: Path) -> list[Path]:
    """Write the sample corpus and return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "annual_report.pdf": _report_pdf(),
        "conference_paper.pdf": _paper_pdf(),
        "short_report.pdf": _report_pdf(pages=2),
        "internal_memo.docx": _memo_docx(),
    }
    written = []
    for name, data in files.items():
        path = directory / name
        path.write_bytes(data)
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/corpus")
    for path in write_corpus(target):
        print(path)
