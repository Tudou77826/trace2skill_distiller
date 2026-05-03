"""Shared enumerations used across modules."""

from enum import Enum


class Label(str, Enum):
    """Trajectory outcome label."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class SkillType(str, Enum):
    """Type of distilled skill."""
    PROCEDURE = "procedure"
    KNOWLEDGE = "knowledge"
    CHECKLIST = "checklist"
    TROUBLESHOOTING = "troubleshooting"
    REFERENCE = "reference"


class RuleType(str, Enum):
    """Type of skill rule."""
    ALWAYS = "ALWAYS"
    WHEN_THEN = "WHEN_THEN"
    NEVER = "NEVER"
    AVOID = "AVOID"
