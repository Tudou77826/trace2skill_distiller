"""Full preprocessing pipeline: Level 0 → 1 → 2."""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress

from ..config import DistillConfig
from ..llm import LLMClient
from ..models import TrajectorySummary
from ..db import export_session
from .preprocess import preprocess, should_process, CleanedSession
from .extract import (
    detect_intent_boundaries,
    extract_block_summary,
    aggregate_session_summary,
)

import sys
_console_file = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = Console(file=_console_file)


def run_pipeline(
    session_id: str,
    fast_llm: LLMClient,
    config: DistillConfig | None = None,
) -> TrajectorySummary | None:
    """Run full Level 0 → 1 → 2 pipeline on a single session.

    Returns None if session doesn't pass quality filter.
    """
    # Export session
    console.print(f"  Exporting session [cyan]{session_id}[/]...")
    session = export_session(session_id)

    # Level 0: noise filtering
    cleaned = preprocess(session)

    # Quality filter
    cfg = config or DistillConfig.load()
    if not should_process(cleaned, cfg.filter.min_messages, cfg.filter.min_tools):
        console.print(f"  [dim]Skipped: {cleaned.message_count} msgs, {cleaned.tool_count} tools[/]")
        return None

    console.print(
        f"  Level 0 done: {cleaned.message_count} msgs, "
        f"{cleaned.tool_count} tools, "
        f"{len(cleaned.user_anchors)} user anchors"
    )

    # Level 1a: intent boundary detection
    blocks = detect_intent_boundaries(cleaned, fast_llm)
    console.print(f"  Level 1a: detected {len(blocks)} intent blocks")

    # Level 1b: per-block extraction
    block_summaries = []
    for block in blocks:
        summary = extract_block_summary(cleaned, block, fast_llm)
        block_summaries.append(summary)
        console.print(f"    Block {block.block_id}: {block.intent[:50]}")

    # Level 2: session-level aggregation
    trajectory = aggregate_session_summary(cleaned, block_summaries, fast_llm)
    console.print(
        f"  Level 2 done: [green]{trajectory.label}[/] "
        f"(score: {trajectory.label_score:.2f})"
    )

    return trajectory


def run_batch(
    session_ids: list[str],
    fast_llm: LLMClient,
    config: DistillConfig | None = None,
) -> list[TrajectorySummary]:
    """Run pipeline on multiple sessions."""
    results = []
    with Progress() as progress:
        task = progress.add_task("Preprocessing sessions...", total=len(session_ids))
        for sid in session_ids:
            try:
                result = run_pipeline(sid, fast_llm, config)
                if result:
                    results.append(result)
            except Exception as e:
                console.print(f"  [red]Error processing {sid}: {e}[/]")
            progress.advance(task)
    return results
