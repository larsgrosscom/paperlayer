# paperlayer

Convert PDF and DOCX into clean, structured Markdown for RAG pipelines.

Fully local. No API keys, no network calls, no model downloads.

```bash
pip install paperlayer
```

```python
from paperlayer import parse

doc = parse("report.pdf")
print(doc.markdown)                    # one Markdown string
print(doc.blocks[0].type, doc.blocks[0].page)   # 'heading', 1
print(doc.outline())      # [(1, 'Quarterly Report'), (2, 'Revenue Breakdown')]
```

## What it does

Raw text extraction gives you a wall of strings. paperlayer gives you a document:

- **Reading order first.** Two-column pages are read column by column, not spliced line by line across the gutter.
- **Tables as tables.** Reconstructed from ruling lines or from whitespace alignment, emitted as real Markdown tables with a detected header row.
- **Heading hierarchy from typography.** Font size and weight relative to the body text decide the level, with section numbering and text shape as corroboration, not a regex on `^\d+\.`.
- **Page furniture removed.** Running headers, footers and page numbers are detected by repetition across pages and stripped. `keep_headers=True` keeps them, tagged.
- **Footnotes kept and reattached.** The note text travels with the block that references it, so the claim and its qualification land in the same retrieval chunk.
- **Every block is addressable.** Page number, block type, heading level, bounding box and the font metrics behind the classification.

## Before and after

A two-column paper. The baseline is `pdfplumber.Page.extract_text()`, which is what most pipelines use today.

**Before.** The two columns are interleaved, and every sentence is spliced with an unrelated one:

```text
1. Experiments
We evaluate the approach on three public Recovering reading order first is what makes
benchmarks and observe consistent gains the remaining stages tractable, since every
over the strongest published baseline. The later decision is conditioned on a coherent
improvement is largest on the long-document linear sequence of text. Removing it costs
split, where context fragmentation dominates roughly nine points of exact match, more
the error profile of prior systems. than any other single ablation we ran.
```

**After.** Columns separated, lines joined into paragraphs, heading marked:

```markdown
# 1. Experiments

We evaluate the approach on three public benchmarks and observe consistent gains
over the strongest published baseline. The improvement is largest on the
long-document split, where context fragmentation dominates the error profile of
prior systems.

Recovering reading order first is what makes the remaining stages tractable,
since every later decision is conditioned on a coherent linear sequence of text.
Removing it costs roughly nine points of exact match, more than any other single
ablation we ran.
```

Embed the "before" text and you have indexed sentences that do not exist.

## The block model

`doc.blocks` is a list of `Block`, in reading order:

```python
@dataclass
class Block:
    type: BlockType        # heading | paragraph | list | table | caption | footnote | code
    text: str              # normalised plain text (what you embed)
    page: int | None       # 1-based; None for DOCX
    order: int             # position in reading order
    level: int | None      # heading level 1-6, or list nesting depth
    bbox: BBox | None      # position on the page
    style: StyleInfo | None    # size, size_ratio, bold_ratio (the evidence)
    table: TableData | None    # rows, header, ruled
    footnotes: list[Footnote]  # attached to the block that references them
    meta: dict[str, Any]       # list items, caption labels, artifact tags
```

`block.markdown` renders that block alone, which is the natural unit for a chunker:

```python
for block in doc.blocks:
    if block.type in ("paragraph", "list", "table"):
        index.add(text=block.markdown, metadata={"page": block.page, "type": block.type})
```

`StyleInfo` is kept so you can see *why* something became an `h2` rather than guess:

```python
h = doc.headings()[0]
h.style.size, h.style.size_ratio, h.style.is_bold   # 18.0, 1.636, True
```

## Options

Zero config for the happy path; everything is tunable when you need it.

```python
doc = parse("report.pdf", keep_headers=False, table_mode="markdown")
```

| Option | Default | Effect |
| --- | --- | --- |
| `keep_headers` | `False` | Keep running headers, footers and page numbers, tagged `meta["artifact"]` |
| `table_mode` | `"markdown"` | `markdown`, `html` (merged cells), `csv`, `text` (cheapest in tokens), `drop` |
| `footnote_mode` | `"inline"` | `inline` (definition after the referencing block), `end`, `drop` |
| `pages` | `None` | 1-based inclusive range, e.g. `(1, 10)` |
| `password` | `None` | For encrypted PDFs |
| `detect_columns` | `True` | Multi-column reading order |
| `detect_unruled_tables` | `True` | Whitespace-aligned table recovery |
| `dehyphenate` | `True` | Join words split across a line break |

The full set is on `ParseOptions`; any field can be passed as a keyword argument to `parse`.

## Benchmark

`benchmarks/run_benchmark.py` runs paperlayer and the naive baseline over a folder and counts both with `tiktoken`. With no folder it generates a synthetic corpus containing the hard cases (running headers, ruled tables, footnotes, two columns, hyphenation).

```bash
python benchmarks/run_benchmark.py --docs ~/my/documents
```

On the bundled corpus, with default settings:

| Document | Baseline tokens | paperlayer tokens | Reduction | Headings | Tables | Lists | Footnotes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `annual_report.pdf` | 1,027 | 1,061 | -3.3% | 12 | 6 | 0 | 6 |
| `conference_paper.pdf` | 547 | 506 | 7.5% | 5 | 0 | 0 | 0 |
| `internal_memo.docx` | 79 | 152 | -92.4% | 4 | 1 | 2 | 1 |
| `short_report.pdf` | 342 | 373 | -9.1% | 4 | 2 | 0 | 2 |
| **Total / median** | **1,995** | **2,092** | **-6.2%** | **25** | **9** | **2** | **9** |

Median token change **-6.2%**, i.e. the default output is about 6% *larger* than raw text, with 97% of baseline content words retained.

**Read that honestly.** Token reduction is not what this library buys you:

- Markdown table syntax costs tokens. `table_mode="text"` turns the same corpus into a **5.1% median reduction** (`benchmarks/results-text.md`), so pick that if tokens are the binding constraint and you do not need machine-readable tables.
- The DOCX row is `-92%` because the naive baseline silently drops every table cell in the document. paperlayer emits more tokens there because it emits more *content*.
- Stripping furniture genuinely saves tokens (that is the whole of the 8.4% on `annual_report.pdf` in text mode), but on short documents the structural markup costs about as much as the furniture saved.

What you actually get is in the right-hand columns: 25 headings, 9 tables and 9 footnotes recovered as structure rather than as undifferentiated text, and no spliced columns. Token count is roughly a wash.

Run it on your own corpus before believing any of these numbers; the synthetic documents are designed to exercise the hard paths, not to be representative of yours.

## How it works

The PDF path uses pdfplumber for exactly two things: positioned characters and vector edges. Everything structural is paperlayer's own code: characters into words (space glyphs, gap width, and superscript state all break a word), words into lines, gutter detection, table grids from ruling-line connectivity, repetition analysis across pages, footnote zones. The DOCX path resolves `w:basedOn` style chains, `numbering.xml` list levels and body element order by hand, because python-docx does not expose them usably.

Stage order is documented in `src/paperlayer/pipeline/__init__.py`; each stage is a pure function of the intermediate representation and is tested without a file on disk.

## Command line

```bash
paperlayer report.pdf                       # Markdown to stdout
paperlayer report.pdf --json -o blocks.json # structured blocks
paperlayer report.pdf --keep-headers --stats
```

## Limitations

- **Scanned PDFs are not OCR'd.** Pages with no extractable text produce a warning in `doc.warnings` and no blocks. Run OCR first.
- **Rotated text is dropped.** Sideways table headers and watermarks cannot be placed in a linear reading order, and interleaving them wrongly is worse than omitting them.
- **Single-page documents keep their furniture.** Repetition is the only reliable signal for a running header, and one page gives none.
- **Images are not extracted.** A figure caption is captured; the figure is not.
- **Unruled table detection is conservative by design.** It would rather miss a table than turn prose into a mangled grid.
- **Equations become plain text.** No LaTeX reconstruction.

## Requirements

Python 3.10+. Two dependencies, both permissively licensed and both imported lazily: [pdfplumber](https://github.com/jsvine/pdfplumber) (MIT) and [python-docx](https://github.com/python-openxml/python-docx) (MIT). No AGPL or GPL-family code, and notably not PyMuPDF.

`import paperlayer` does not import either backend; they load only when a document of that format is parsed.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && mypy
```

The test suite generates its own PDF and DOCX fixtures in-process (`tests/pdfgen.py`, `tests/docxgen.py`), so the repository carries no binary test data and every fixture states its own typography explicitly.

## Author

Built by Lars Gross. More of my work at [larsgross.com](https://larsgross.com).

## License

Apache-2.0. See [LICENSE](LICENSE).
