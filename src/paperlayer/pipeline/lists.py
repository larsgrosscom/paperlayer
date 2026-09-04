"""List detection.

Two things have to be recovered that plain text extraction destroys: which
lines are items (as opposed to a paragraph that happens to start with a dash),
and how deeply each item is nested. Nesting comes from the left edge of the
item text, clustered per document, because indent widths vary by producer and
absolute point values mean nothing on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._text import split_list_marker

__all__ = ["IndentScale", "ListItem", "item_start"]

#: Indents within this many points are the same nesting level.
_INDENT_TOL = 6.0


@dataclass(slots=True)
class ListItem:
    """One item of a list, possibly spanning several visual lines."""

    marker: str
    ordered: bool
    level: int
    lines: list[str] = field(default_factory=list)
    #: Left edge of the marker, used to resolve nesting.
    indent: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(self.lines).strip()


def item_start(text: str) -> tuple[str, str, bool] | None:
    """``(marker, remainder, ordered)`` if this text opens a list item."""
    return split_list_marker(text)


class IndentScale:
    """Maps left-edge positions to nesting levels, learned per document.

    Built from every list marker in the document rather than per list, so that
    a nested item appearing alone in one list still lands at the depth its
    indent implies elsewhere.
    """

    __slots__ = ("_stops",)

    def __init__(self, indents: list[float]) -> None:
        self._stops = self._cluster(indents)

    @staticmethod
    def _cluster(indents: list[float]) -> list[float]:
        stops: list[float] = []
        for value in sorted(indents):
            if stops and value - stops[-1] <= _INDENT_TOL:
                continue
            stops.append(value)
        return stops

    def level(self, indent: float) -> int:
        """Zero-based nesting depth for a marker at ``indent``."""
        for index, stop in enumerate(self._stops):
            if indent <= stop + _INDENT_TOL:
                return index
        return max(len(self._stops) - 1, 0)

    def __len__(self) -> int:
        return len(self._stops)
