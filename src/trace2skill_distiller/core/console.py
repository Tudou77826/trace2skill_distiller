"""Shared Rich Console — all modules use this single instance."""

from rich.console import Console

console = Console(stderr=True)
