"""Exception hierarchy for paperlayer.

Every error raised by the public API derives from :class:`PaperlayerError`, so a
caller can wrap a whole parse in a single ``except`` without catching unrelated
``ValueError``s from elsewhere.
"""

from __future__ import annotations

__all__ = [
    "MissingBackendError",
    "PaperlayerError",
    "ParseError",
    "PasswordRequiredError",
    "UnsupportedFormatError",
]


class PaperlayerError(Exception):
    """Base class for every error paperlayer raises."""


class UnsupportedFormatError(PaperlayerError):
    """The input is not a PDF or DOCX, or the format could not be determined."""


class MissingBackendError(PaperlayerError):
    """A reader backend is not installed.

    Only reachable when someone installs paperlayer with ``--no-deps`` or
    vendors it partially; the message carries the exact ``pip install`` fix.
    """

    def __init__(self, package: str, fmt: str) -> None:
        self.package = package
        self.format = fmt
        super().__init__(
            f"Reading {fmt.upper()} files requires the {package!r} package, which is "
            f"missing from this environment. Install it with: pip install {package}"
        )


class PasswordRequiredError(PaperlayerError):
    """The PDF is encrypted and the supplied password (if any) did not work."""


class ParseError(PaperlayerError):
    """The document is malformed beyond what the readers can recover from."""
