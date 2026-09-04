"""The public surface: dispatch, errors, serialisation, CLI, import cost."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import paperlayer
from paperlayer import (
    UnsupportedFormatError,
    detect_format,
    parse,
    parse_bytes,
)


class TestFormatDetection:
    def test_pdf_bytes(self, simple_report: bytes) -> None:
        assert detect_format(simple_report) == "pdf"

    def test_docx_bytes(self, structured_docx: bytes) -> None:
        assert detect_format(structured_docx) == "docx"

    def test_content_beats_the_extension(self, tmp_path: Path, simple_report: bytes) -> None:
        # A PDF named .docx still parses as a PDF.
        path = tmp_path / "mislabelled.docx"
        path.write_bytes(simple_report)
        assert detect_format(path) == "pdf"
        assert parse(path).headings()

    def test_a_zip_that_is_not_a_docx_is_rejected(self, tmp_path: Path) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", "<x/>")
        with pytest.raises(UnsupportedFormatError, match="not a DOCX"):
            detect_format(buffer.getvalue())

    def test_plain_text_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFormatError):
            detect_format(b"just some text, not a document")

    def test_a_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse(tmp_path / "nope.pdf")


class TestInputTypes:
    def test_path(self, tmp_path: Path, simple_report: bytes) -> None:
        path = tmp_path / "report.pdf"
        path.write_bytes(simple_report)
        assert parse(path).headings()[0].text == "Quarterly Report"

    def test_string_path(self, tmp_path: Path, simple_report: bytes) -> None:
        path = tmp_path / "report.pdf"
        path.write_bytes(simple_report)
        assert parse(str(path)).headings()

    def test_bytes(self, simple_report: bytes) -> None:
        assert parse(simple_report).headings()

    def test_open_file_object(self, tmp_path: Path, simple_report: bytes) -> None:
        path = tmp_path / "report.pdf"
        path.write_bytes(simple_report)
        with path.open("rb") as handle:
            assert parse(handle).headings()

    def test_parse_bytes_with_an_explicit_format(self, structured_docx: bytes) -> None:
        assert parse_bytes(structured_docx, format="docx").headings()

    def test_parse_bytes_rejects_an_unknown_format(self, simple_report: bytes) -> None:
        with pytest.raises(UnsupportedFormatError):
            parse_bytes(simple_report, format="rtf")


class TestSerialisation:
    def test_to_dict_round_trips_through_json(self, ruled_table_pdf: bytes) -> None:
        doc = parse(ruled_table_pdf)
        payload = json.loads(doc.to_json())
        assert payload["source"]["format"] == "pdf"
        assert len(payload["blocks"]) == len(doc.blocks)
        table = next(b for b in payload["blocks"] if b["type"] == "table")
        assert table["table"]["header"] == ["Region", "Revenue", "Growth"]
        assert table["markdown"].startswith("| Region")

    def test_block_markdown_is_self_contained(self, simple_report: bytes) -> None:
        doc = parse(simple_report)
        assert doc.headings()[0].markdown == "# Quarterly Report"

    def test_document_text_has_no_markdown_syntax(self, simple_report: bytes) -> None:
        doc = parse(simple_report)
        assert "#" not in doc.text

    def test_len_and_iteration(self, simple_report: bytes) -> None:
        doc = parse(simple_report)
        assert len(doc) == len(list(doc)) == len(doc.blocks)


class TestImportCost:
    def test_backends_are_not_imported_eagerly(self) -> None:
        code = (
            "import sys, paperlayer;"
            "print([m for m in sys.modules "
            "if m.split('.')[0] in ('pdfplumber','pdfminer','docx','lxml','PIL')])"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "[]", (
            "importing paperlayer must not pull in a document backend"
        )

    def test_version_is_a_single_source_of_truth(self) -> None:
        from importlib.metadata import version

        assert version("paperlayer") == paperlayer.__version__


class TestCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "paperlayer", *args],
            capture_output=True,
            text=True,
        )

    def test_markdown_to_stdout(self, tmp_path: Path, simple_report: bytes) -> None:
        path = tmp_path / "r.pdf"
        path.write_bytes(simple_report)
        result = self._run(str(path))
        assert result.returncode == 0
        assert "# Quarterly Report" in result.stdout

    def test_json_output(self, tmp_path: Path, ruled_table_pdf: bytes) -> None:
        path = tmp_path / "r.pdf"
        path.write_bytes(ruled_table_pdf)
        result = self._run(str(path), "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout)["blocks"]

    def test_writes_to_a_file(self, tmp_path: Path, simple_report: bytes) -> None:
        src = tmp_path / "r.pdf"
        src.write_bytes(simple_report)
        out = tmp_path / "r.md"
        assert self._run(str(src), "-o", str(out)).returncode == 0
        assert "# Quarterly Report" in out.read_text(encoding="utf-8")

    def test_a_bad_file_exits_nonzero_with_a_message(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"not a pdf at all")
        result = self._run(str(path))
        assert result.returncode == 1
        assert "paperlayer:" in result.stderr
