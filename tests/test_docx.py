"""End-to-end behaviour of the DOCX path."""

from __future__ import annotations

from docxgen import build_docx, paragraph, table
from paperlayer import parse


def test_heading_styles_drive_levels(structured_docx: bytes) -> None:
    doc = parse(structured_docx)
    assert doc.outline() == [(1, "Introduction"), (2, "Scope")]


def test_source_metadata(structured_docx: bytes) -> None:
    doc = parse(structured_docx)
    assert doc.source.format == "docx"
    assert doc.source.n_pages is None, "DOCX has no fixed pagination"
    assert all(block.page is None for block in doc.blocks)


def test_body_order_is_preserved_across_tables(structured_docx: bytes) -> None:
    doc = parse(structured_docx)
    kinds = [b.type for b in doc.blocks]
    assert kinds.index("table") > kinds.index("list")
    assert kinds.index("caption") > kinds.index("table")


class TestLists:
    def test_numbering_gives_nesting(self, structured_docx: bytes) -> None:
        bullets = parse(structured_docx).of_type("list")[0]
        assert [i["level"] for i in bullets.meta["items"]] == [0, 1, 0]
        assert all(not i["ordered"] for i in bullets.meta["items"])

    def test_separate_numbering_makes_separate_lists(self, structured_docx: bytes) -> None:
        lists = parse(structured_docx).of_type("list")
        assert len(lists) == 2
        assert all(i["ordered"] for i in lists[1].meta["items"])

    def test_ordered_list_is_renumbered_in_markdown(self, structured_docx: bytes) -> None:
        markdown = parse(structured_docx).markdown
        assert "1. Step one" in markdown
        assert "2. Step two" in markdown


class TestTables:
    def test_cells_survive(self, structured_docx: bytes) -> None:
        data = parse(structured_docx).tables()[0]
        assert data.header == ["Region", "Revenue"]
        assert data.rows == [["EMEA", "1,204"], ["APAC", "980"]]

    def test_merged_cells_are_expanded_not_dropped(self) -> None:
        # A two-column grid whose first row is one merged cell spanning both.
        merged = (
            "<w:tbl>"
            '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
            "<w:p><w:r><w:t>Spanning header</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>left</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>right</w:t></w:r></w:p></w:tc></w:tr>"
            "</w:tbl>"
        )
        doc = parse(build_docx([merged]))
        rows = doc.tables()[0].all_rows()
        assert rows[0] == ["Spanning header", ""]
        assert rows[1] == ["left", "right"]

    def test_every_cell_of_a_plain_grid_is_kept(self) -> None:
        grid = [["a1", "b1"], ["a2", "b2"], ["a3", "b3"]]
        doc = parse(build_docx([table(grid)]))
        assert doc.tables()[0].all_rows() == grid


class TestFootnotes:
    def test_reference_is_inlined_and_note_attached(self, structured_docx: bytes) -> None:
        doc = parse(structured_docx)
        block = next(b for b in doc.blocks if "background" in b.text)
        assert "[^1]" in block.text
        assert block.footnotes[0].text == "A clarifying footnote from Word."

    def test_markdown_places_the_definition_after_the_block(
        self, structured_docx: bytes
    ) -> None:
        markdown = parse(structured_docx).markdown
        assert "[^1]: A clarifying footnote from Word." in markdown


def test_caption_style_is_recognised(structured_docx: bytes) -> None:
    caption = parse(structured_docx).of_type("caption")[0]
    assert caption.text == "Table 1. Revenue by region."
    assert caption.meta["caption_kind"] == "table"


def test_direct_formatting_falls_back_to_font_metrics(unstyled_docx: bytes) -> None:
    doc = parse(unstyled_docx)
    assert doc.outline() == [(1, "Project Overview"), (2, "Background")]


def test_style_inheritance_is_resolved() -> None:
    # Heading2 inherits its size from the chain, not from an explicit run.
    doc = parse(build_docx([paragraph("Inherited", style="Heading2")]))
    heading = doc.headings()[0]
    assert heading.style is not None
    assert heading.style.size == 13.0
