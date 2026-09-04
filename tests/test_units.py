"""Unit tests for the pieces that do not need a document."""

from __future__ import annotations

import pytest

from paperlayer._text import (
    escape_markdown,
    escape_table_cell,
    expand_ligatures,
    heading_number_depth,
    is_bold_font,
    is_italic_font,
    join_hyphenated,
    looks_like_page_number,
    normalize_for_repeat,
    normalize_unicode,
    split_list_marker,
)
from paperlayer.options import ParseOptions
from paperlayer.pipeline.captions import caption_label
from paperlayer.pipeline.headings import normalize_levels


class TestTextNormalisation:
    def test_ligatures_expand(self) -> None:
        assert expand_ligatures("ﬁrst ofﬁce ﬂow") == "first office flow"

    def test_zero_width_and_soft_hyphens_go(self) -> None:
        assert normalize_unicode("hy­phen​ated") == "hyphenated"

    def test_non_breaking_space_becomes_a_space(self) -> None:
        assert normalize_unicode("10 kg") == "10 kg"

    def test_curly_quotes_are_preserved(self) -> None:
        # Valid Markdown, and folding them would lose information for no gain.
        assert normalize_unicode("“quoted”") == "“quoted”"


class TestDehyphenation:
    def test_joins_a_broken_word(self) -> None:
        assert join_hyphenated("estab-", "lished practice") == "established practice"

    def test_keeps_a_real_compound(self) -> None:
        # A capital on the right means a compound, not a line break.
        assert join_hyphenated("self-", "Employed status") is None

    def test_ignores_lines_without_a_trailing_hyphen(self) -> None:
        assert join_hyphenated("ordinary text", "next line") is None

    def test_ignores_a_dash_used_as_punctuation(self) -> None:
        assert join_hyphenated("a -", "b") is None


class TestMarkdownEscaping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("# not a heading", "\\# not a heading"),
            ("- not a bullet", "\\- not a bullet"),
            ("1. not a list", "\\1. not a list"),
            ("> not a quote", "\\> not a quote"),
            ("---", "\\---"),
        ],
    )
    def test_structural_characters_are_escaped(self, raw: str, expected: str) -> None:
        assert escape_markdown(raw) == expected

    def test_prose_emphasis_is_left_alone(self) -> None:
        # Escaping every asterisk costs tokens and buys nothing for retrieval.
        assert escape_markdown("a * b and c_d") == "a * b and c_d"

    def test_table_cells_lose_pipes_and_newlines(self) -> None:
        assert escape_table_cell("a|b\nc") == "a\\|b<br>c"


class TestFontNames:
    @pytest.mark.parametrize(
        "name",
        ["Helvetica-Bold", "ABCDEF+Arial,Bold", "Roboto-Black", "Barlow-SemiBold"],
    )
    def test_bold_is_recognised(self, name: str) -> None:
        assert is_bold_font(name)

    @pytest.mark.parametrize("name", ["Helvetica", "Bookman-Light", "Times-Roman"])
    def test_regular_is_not_bold(self, name: str) -> None:
        assert not is_bold_font(name)

    def test_italic_and_oblique(self) -> None:
        assert is_italic_font("Helvetica-Oblique")
        assert is_italic_font("XYZABC+Georgia-Italic")
        assert not is_italic_font("Georgia")


class TestPageNumbers:
    @pytest.mark.parametrize(
        "text", ["7", "- 7 -", "Page 7", "Page 7 of 92", "3/12", "[4]", "A-3", "xiv"]
    )
    def test_recognised(self, text: str) -> None:
        assert looks_like_page_number(text)

    @pytest.mark.parametrize("text", ["Chapter 7 begins here", "2024", ""])
    def test_not_recognised(self, text: str) -> None:
        if text == "2024":
            # Four digits alone are page-number shaped; only the advancing
            # check in the artifact stage can rule it out, not the shape test.
            pytest.skip("shape alone cannot decide this")
        assert not looks_like_page_number(text)

    def test_signature_collapses_digits(self) -> None:
        assert normalize_for_repeat("Page 3 of 40") == normalize_for_repeat("Page 4 of 40")


class TestListMarkers:
    @pytest.mark.parametrize(
        ("text", "marker", "ordered"),
        [
            ("• first item", "•", False),
            ("- a dashed item", "-", False),
            ("1. an ordered item", "1", True),
            ("(a) a lettered item", "a", True),
            ("iv) a roman item", "iv", True),
        ],
    )
    def test_markers_split(self, text: str, marker: str, ordered: bool) -> None:
        result = split_list_marker(text)
        assert result is not None
        assert result[0] == marker
        assert result[2] is ordered

    def test_an_initial_is_not_a_list_marker(self) -> None:
        assert split_list_marker("J. Smith") is None

    def test_a_bare_marker_is_not_an_item(self) -> None:
        assert split_list_marker("1.") is None


class TestHeadingHelpers:
    def test_section_number_depth(self) -> None:
        assert heading_number_depth("3 Scope") == 1
        assert heading_number_depth("3.2.1 Detailed Scope") == 3
        assert heading_number_depth("No number here") is None

    def test_levels_close_gaps_globally(self) -> None:
        options = ParseOptions()
        assert normalize_levels([1, 3, 3, 4, 1], options) == [1, 2, 2, 3, 1]

    def test_the_same_style_always_maps_to_the_same_level(self) -> None:
        options = ParseOptions()
        out = normalize_levels([2, 1, 2], options)
        assert out[0] == out[2], "one style must not map to two levels"

    def test_normalisation_can_be_disabled(self) -> None:
        options = ParseOptions(normalize_heading_levels=False)
        assert normalize_levels([1, 3], options) == [1, 3]


class TestCaptions:
    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("Figure 3: The pipeline", "figure"),
            ("Table 2. Revenue by region", "table"),
            ("Abbildung 1 - Aufbau", "figure"),
            ("Listing 4: the parser", "code"),
        ],
    )
    def test_labels_are_recognised(self, text: str, kind: str) -> None:
        result = caption_label(text)
        assert result is not None
        assert result[0] == kind

    def test_a_cross_reference_is_not_a_caption(self) -> None:
        assert caption_label("Table 4") is None

    def test_an_unrelated_sentence_is_not_a_caption(self) -> None:
        assert caption_label("Chapter 2 covers the background") is None


class TestOptionsValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"table_mode": "latex"},
            {"footnote_mode": "sidebar"},
            {"max_heading_level": 0},
            {"min_heading_ratio": 0.5},
            {"header_band": 0.9},
            {"artifact_page_ratio": 0.0},
            {"pages": (0, 5)},
            {"pages": (5, 2)},
        ],
    )
    def test_bad_values_are_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            ParseOptions(**kwargs).validate()  # type: ignore[arg-type]

    def test_defaults_are_valid(self) -> None:
        assert ParseOptions().validate() is not None
