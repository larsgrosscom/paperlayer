# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-09-04

### Changed

- README: added an author section linking to larsgross.com. No code changes;
  this release exists so the project description on PyPI matches the
  repository.

## [0.1.0] - 2026-09-04

First release.

### Added

- `parse()` for PDF and DOCX from a path, bytes or an open binary file, with
  format detection by content rather than by file extension.
- `Document` / `Block` model: reading-order blocks carrying page number, block
  type, heading level, bounding box, table data, attached footnotes and the
  font metrics behind each classification.
- Markdown rendering with `markdown`, `html`, `csv`, `text` and `drop` table
  modes, and `inline`, `end` and `drop` footnote modes.
- Multi-column reading order, including splitting lines wrongly merged across
  a gutter.
- Table reconstruction from vector ruling lines, including horizontally merged
  cells, plus a conservative whitespace-alignment fallback.
- Heading hierarchy from font size and weight relative to body text,
  corroborated by section numbering and text shape.
- Running header, footer and page-number removal by cross-page repetition,
  with `keep_headers=True` to retain and tag them.
- Footnote recovery and reattachment to the referencing block.
- List detection with nesting from indentation (PDF) and `numbering.xml`
  (DOCX).
- Figure and table caption detection, with table captions cross-referenced to
  the table they name.
- `paperlayer` command line interface and `python -m paperlayer`.
- Benchmark script comparing against naive raw-text extraction with `tiktoken`
  token counts and a content-retention check.

[Unreleased]: https://github.com/larsgrosscom/paperlayer/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/larsgrosscom/paperlayer/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/larsgrosscom/paperlayer/releases/tag/v0.1.0
