"""Distillation strategies."""

from .base import DistillationStrategy
from .llm_distill import LLMDistillationStrategy

__all__ = ["DistillationStrategy", "LLMDistillationStrategy"]
