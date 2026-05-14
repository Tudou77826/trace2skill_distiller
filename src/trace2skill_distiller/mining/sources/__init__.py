"""Data source implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SessionSource
from .opencode import OpenCodeSource
from .chrys import ChrysSource
from .codeagent import CodeAgentSource
from .claudecode import ClaudeCodeSource

if TYPE_CHECKING:
    from ...core.config import SourceConfig

__all__ = ["SessionSource", "OpenCodeSource", "ChrysSource", "CodeAgentSource", "ClaudeCodeSource", "create_source"]


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
    elif source_type == "codeagent":
        return CodeAgentSource(
            db_path=config.codeagent.db_path,
        )
    elif source_type == "claudecode":
        return ClaudeCodeSource(
            projects_dir=config.claudecode.projects_dir,
        )
    else:
        raise ValueError(
            f"Unknown source type: '{source_type}'. "
            f"Supported: opencode, chrys, codeagent, claudecode"
        )
