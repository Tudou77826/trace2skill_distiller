"""Data source implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SessionSource
from .opencode import OpenCodeSource
from .chrys import ChrysSource

if TYPE_CHECKING:
    from ...core.config import SourceConfig

__all__ = ["SessionSource", "OpenCodeSource", "ChrysSource", "create_source"]


def create_source(config: SourceConfig) -> SessionSource:
    """Create a SessionSource instance based on SourceConfig.type."""
    source_type = config.type.lower()

    if source_type == "chrys":
        return ChrysSource(
            sessions_dir=config.chrys.sessions_dir or None,
        )
    elif source_type == "opencode":
        return OpenCodeSource(
            db_path=config.opencode.db_path,
            export_command=config.opencode.export_command,
        )
    else:
        raise ValueError(
            f"Unknown source type: '{source_type}'. "
            f"Supported: opencode, chrys"
        )
