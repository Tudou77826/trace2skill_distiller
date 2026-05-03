"""Skill formatter protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ...analysis.types import TopicSkill


@runtime_checkable
class SkillFormatter(Protocol):
    """Skill file formatting protocol.

    Supports multiple output formats:
    - SKILL.md (Claude Code format, current)
    - JSON (structured data)
    - Confluence Wiki
    """

    def write(self, skill: TopicSkill, output_dir: Path, project: str) -> Path:
        """Write a new skill file."""
        ...

    def merge(self, existing_path: Path, new_skill: TopicSkill) -> Path:
        """Merge new content into an existing skill file."""
        ...

    def write_index(self, skills: list[TopicSkill], output_dir: Path, project: str) -> Path:
        """Write an index file listing all skills."""
        ...
