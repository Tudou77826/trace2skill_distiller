"""Output module: formatting, presenting, and persisting results."""

from .types import (
    DistillReport, SessionEntry, TopicEntry, StepTiming,
    LLMUsage, RunState, ShapingResult,
)
from .output_facade import OutputLayer, DefaultOutputLayer

__all__ = [
    "DistillReport", "SessionEntry", "TopicEntry", "StepTiming",
    "LLMUsage", "RunState", "ShapingResult",
    "OutputLayer", "DefaultOutputLayer",
]
