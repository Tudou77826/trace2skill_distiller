"""Report presenter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..types import DistillReport


@runtime_checkable
class ReportPresenter(Protocol):
    """Report presentation protocol.

    Supports multiple formats:
    - HTML report (current)
    - Terminal Rich output
    - Markdown report
    """

    def present(self, report: DistillReport, output_path: Path | None = None) -> str:
        """Present the report and return the rendered content string."""
        ...
