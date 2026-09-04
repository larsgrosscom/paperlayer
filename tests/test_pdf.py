"""End-to-end behaviour of the PDF path."""

from __future__ import annotations

import pytest

from paperlayer import ParseOptions, parse
from pdfgen import Page, build_pdf


def test_headings_come_from_font_size(simple_report: bytes) -> None:
    doc = parse(simple_report)
    outline = doc.outline()
    assert outline == [(1, "Quarterly Report"), (2, "Revenue Breakdown")]


def test_paragraph_lines_are_merged(simple_report: bytes) -> None:
    doc = parse(simple_report)
    paragraphs = doc.of_type("paragraph")
    assert paragraphs[0].text == (
        "This is the opening paragraph of the report and it "
        "continues onto a second line before it ends."
    )


def test_style_evidence_is_kept(simple_report: bytes) -> None:
    doc = parse(simple_report)
    heading = doc.headings()[0]
    assert heading.style is not None
    assert heading.style.size == pytest.approx(18.0)
    assert heading.style.size_ratio > 1.5
    assert heading.style.is_bold


def test_blocks_carry_page_and_order(simple_report: bytes) -> None:
    doc = parse(simple_report)
    assert [b.order for b in doc.blocks] == list(range(len(doc.blocks)))
    assert all(b.page == 1 for b in doc.blocks)


class TestTables:
    def test_ruled_table_becomes_markdown(self, ruled_table_pdf: bytes) -> None:
        doc = parse(ruled_table_pdf)
        markdown = doc.markdown
        assert "| Region | Revenue | Growth |" in markdown
        assert "| --- | --- | --- |" in markdown
        assert "| EMEA | 1,204 | 12% |" in markdown

    def test_header_row_is_detected(self, ruled_table_pdf: bytes) -> None:
        table = parse(ruled_table_pdf).tables()[0]
        assert table.header == ["Region", "Revenue", "Growth"]
        assert table.rows == [["EMEA", "1,204", "12%"], ["APAC", "980", "8%"]]
        assert table.ruled is True

    def test_table_text_is_not_duplicated_in_prose(self, ruled_table_pdf: bytes) -> None:
        doc = parse(ruled_table_pdf)
        prose = " ".join(b.text for b in doc.blocks if b.type != "table")
        assert "EMEA" not in prose

    def test_caption_is_its_own_block_and_links_to_the_table(
        self, ruled_table_pdf: bytes
    ) -> None:
        doc = parse(ruled_table_pdf)
        caption = doc.of_type("caption")[0]
        assert caption.meta["caption_kind"] == "table"
        assert caption.meta["label"] == "Table 1"
        table_block = doc.of_type("table")[0]
        assert table_block.meta["caption"] == caption.text

    @pytest.mark.parametrize(
        ("mode", "needle"),
        [
            ("html", "<th>Region</th>"),
            ("csv", "```csv"),
            ("text", "Region\tRevenue\tGrowth"),
        ],
    )
    def test_table_modes(self, ruled_table_pdf: bytes, mode: str, needle: str) -> None:
        doc = parse(ruled_table_pdf, table_mode=mode)
        assert needle in doc.markdown

    def test_table_mode_drop_removes_it_from_markdown_only(
        self, ruled_table_pdf: bytes
    ) -> None:
        doc = parse(ruled_table_pdf, table_mode="drop")
        assert "EMEA" not in doc.markdown
        assert doc.tables(), "the table must still be available structurally"


class TestArtifacts:
    def test_running_header_and_page_numbers_are_stripped(self, paged_report: bytes) -> None:
        markdown = parse(paged_report).markdown
        assert "ACME Corporation" not in markdown
        assert "Section 1" in markdown and "Section 3" in markdown

    def test_keep_headers_retains_and_tags_them(self, paged_report: bytes) -> None:
        doc = parse(paged_report, keep_headers=True)
        artifacts = [b for b in doc.blocks if b.meta.get("artifact")]
        kinds = {b.meta["artifact"] for b in artifacts}
        assert "header" in kinds
        assert "page_number" in kinds
        assert any("ACME Corporation" in b.text for b in artifacts)

    def test_single_page_documents_are_left_alone(self) -> None:
        # With one page there is no repetition to observe, so nothing may be
        # removed on suspicion alone.
        page = Page()
        page.text(72, 40, "Looks Like A Header", size=8)
        page.text(72, 200, "The only paragraph of body text on this page.", size=11)
        page.text(300, 760, "1", size=9)
        doc = parse(build_pdf([page]))
        assert "Looks Like A Header" in doc.markdown


class TestFootnotes:
    def test_footnote_attaches_to_the_referencing_block(self, paged_report: bytes) -> None:
        doc = parse(paged_report)
        block = next(b for b in doc.blocks if b.page == 2 and b.type == "paragraph")
        assert [f.marker for f in block.footnotes] == ["2"]
        assert block.footnotes[0].text.startswith("Footnote 2")

    def test_reference_marker_is_inlined(self, paged_report: bytes) -> None:
        doc = parse(paged_report)
        block = next(b for b in doc.blocks if b.page == 1 and b.type == "paragraph")
        assert "[^1]" in block.text

    def test_page_number_does_not_leak_into_the_note(self, paged_report: bytes) -> None:
        doc = parse(paged_report)
        for note in doc.footnotes():
            assert note.text.endswith("more depth.")

    def test_footnote_mode_end_moves_definitions_to_the_bottom(
        self, paged_report: bytes
    ) -> None:
        markdown = parse(paged_report, footnote_mode="end").markdown
        body_end = markdown.index("[^1]: ")
        assert body_end > markdown.index("Section 3")

    def test_footnote_mode_drop_removes_markers_and_text(self, paged_report: bytes) -> None:
        markdown = parse(paged_report, footnote_mode="drop").markdown
        assert "[^1]" not in markdown
        assert "explaining the claim" not in markdown


class TestColumns:
    def test_columns_are_read_one_at_a_time(self, two_column_pdf: bytes) -> None:
        markdown = parse(two_column_pdf).markdown
        assert markdown.index("Alpha six") < markdown.index("Beta one")

    def test_no_line_splices_two_columns_together(self, two_column_pdf: bytes) -> None:
        doc = parse(two_column_pdf)
        for block in doc.of_type("paragraph"):
            assert not ("Alpha" in block.text and "Beta" in block.text)

    def test_detection_can_be_disabled(self, two_column_pdf: bytes) -> None:
        doc = parse(two_column_pdf, detect_columns=False)
        markdown = doc.markdown
        # Without column detection the lines interleave, which is exactly the
        # failure this option exists to let you observe.
        assert markdown.index("Beta one") < markdown.index("Alpha six")


class TestLists:
    def test_bullets_and_nesting(self, list_pdf: bytes) -> None:
        doc = parse(list_pdf)
        block = doc.of_type("list")[0]
        items = block.meta["items"]
        assert [i["level"] for i in items] == [0, 0, 1, 0]
        assert items[2]["text"] == "including in air-gapped environments"

    def test_ordered_items_are_not_headings(self, list_pdf: bytes) -> None:
        doc = parse(list_pdf)
        assert all("Install the package" not in h.text for h in doc.headings())
        ordered = doc.of_type("list")[-1]
        assert all(item["ordered"] for item in ordered.meta["items"])

    def test_markdown_renders_nesting(self, list_pdf: bytes) -> None:
        markdown = parse(list_pdf).markdown
        assert "  - including in air-gapped environments" in markdown
        assert "1. Install the package" in markdown
        assert "2. Call parse" in markdown


class TestNumberedHeadings:
    def test_section_numbers_drive_levels(self) -> None:
        page = Page()
        page.text(72, 90, "1. Requirements", size=14, bold=True)
        page.text(72, 120, "Body prose introducing the requirements section.", size=11)
        page.text(72, 150, "1.1 Detailed Notes", size=12, bold=True)
        page.text(72, 180, "More body prose under the detailed notes heading.", size=11)
        doc = parse(build_pdf([page]))
        assert doc.outline() == [(1, "1. Requirements"), (2, "1.1 Detailed Notes")]


def test_page_range_limits_parsing(paged_report: bytes) -> None:
    doc = parse(paged_report, pages=(2, 2))
    assert {b.page for b in doc.blocks} == {2}


def test_scanned_pages_produce_a_warning() -> None:
    page = Page()  # nothing drawn at all
    doc = parse(build_pdf([page, page]))
    assert any("no extractable text" in w for w in doc.warnings)
    assert doc.blocks == []


def test_options_object_and_kwargs_combine() -> None:
    options = ParseOptions(table_mode="csv", keep_headers=True)
    page = Page()
    page.text(72, 100, "Title Here", size=16, bold=True)
    doc = parse(build_pdf([page]), options=options, table_mode="html")
    assert doc.options is not None
    assert doc.options.table_mode == "html", "explicit kwargs beat the options object"
    assert doc.options.keep_headers is True


class TestWrappedHeadings:
    def test_a_title_set_over_two_lines_is_one_heading(self) -> None:
        page = Page()
        page.text(72, 100, "Layout Aware Document Parsing", size=17, bold=True)
        page.text(72, 122, "For Retrieval Augmented Generation", size=17, bold=True)
        y = 170.0
        for line in [
            "Body prose beginning the document proper and running on",
            "for several lines so that the body text size is unambiguous",
            "and the bold-saturation guard does not suppress the title.",
            "A fourth line of ordinary prose to settle the statistics.",
        ]:
            page.text(72, y, line, size=11)
            y += 15
        doc = parse(build_pdf([page]))
        assert doc.outline() == [
            (1, "Layout Aware Document Parsing For Retrieval Augmented Generation")
        ]

    def test_two_separate_headings_stay_separate(self) -> None:
        # Same style, but a full paragraph of space between them.
        page = Page()
        page.text(72, 100, "First Section", size=15, bold=True)
        page.text(72, 130, "Body prose under the first section heading.", size=11)
        page.text(72, 190, "Second Section", size=15, bold=True)
        page.text(72, 220, "Body prose under the second section heading.", size=11)
        doc = parse(build_pdf([page]))
        assert doc.outline() == [(1, "First Section"), (1, "Second Section")]
