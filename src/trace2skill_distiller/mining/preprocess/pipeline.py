"""Full preprocessing pipeline: Level 0 → 1 → 2."""

from __future__ import annotations

from rich.progress import Progress

from ...llm import LLMClient
from ...core.config import DistillConfig
from ...core.console import console
from ..types import TrajectorySummary
from ..sources.base import SessionSource
from .compress import preprocess, should_process, CleanedSession
from .extract import (
    detect_intent_boundaries,
    extract_block_summary,
    aggregate_session_summary,
)


def run_pipeline(
    session_id: str,
    fast_llm: LLMClient,
    source: SessionSource,
    config: DistillConfig | None = None,
) -> TrajectorySummary | None:
    """Run full Level 0 → 1 → 2 pipeline on a single session."""
    console.print(f"  Exporting session [cyan]{session_id}[/]...")
    session = source.get_session(session_id)

    cleaned = preprocess(session)

    cfg = config or DistillConfig.load()
    if not should_process(cleaned, cfg.filter.min_messages, cfg.filter.min_tools):
        console.print(f"  [dim]Skipped: {cleaned.message_count} msgs, {cleaned.tool_count} tools[/]")
        return None

    console.print(
        f"  Level 0 done: {cleaned.message_count} msgs, "
        f"{cleaned.tool_count} tools, "
        f"{len(cleaned.user_anchors)} user anchors"
    )

    blocks = detect_intent_boundaries(cleaned, fast_llm)
    console.print(f"  Level 1a: detected {len(blocks)} intent blocks")

    block_summaries = []
    for block in blocks:
        summary = extract_block_summary(cleaned, block, fast_llm)
        block_summaries.append(summary)
        console.print(f"    Block {block.block_id}: {block.intent[:50]}")

    trajectory = aggregate_session_summary(cleaned, block_summaries, fast_llm)
    console.print(
        f"  Level 2 done: [green]{trajectory.label}[/] "
        f"(score: {trajectory.label_score:.2f})"
    )

    return trajectory


def run_batch(
    session_ids: list[str],
    fast_llm: LLMClient,
    source: SessionSource,
    config: DistillConfig | None = None,
) -> list[TrajectorySummary]:
    """Run pipeline on multiple sessions."""
    results = []
    with Progress() as progress:
        task = progress.add_task("Preprocessing sessions...", total=len(session_ids))
        for sid in session_ids:
            try:
                result = run_pipeline(sid, fast_llm, source, config)
                if result:
                    results.append(result)
            except Exception as e:
                console.print(f"  [red]Error processing {sid}: {e}[/]")
            progress.advance(task)
    return results
