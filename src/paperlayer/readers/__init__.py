"""Format backends.

Deliberately empty of imports: pulling in a reader here would drag pdfplumber
or python-docx into every ``import paperlayer``, which is exactly the cost the
lazy-import rule exists to avoid.
"""

from __future__ import annotations

__all__: list[str] = []
