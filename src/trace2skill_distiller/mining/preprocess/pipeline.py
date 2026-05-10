"""Full preprocessing pipeline: Level 0 → 1 → 2."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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

logger = logging.getLogger(__name__)


def _short(sid: str) -> str:
    return f"{sid[:16]}…"


def _fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def run_pipeline(
    session_id: str,
    fast_llm: LLMClient,
    source: SessionSource,
    config: DistillConfig | None = None,
    *,
    quiet: bool = False,
    max_workers: int = 1,
) -> TrajectorySummary | None:
    """Run full Level 0 → 1 → 2 pipeline on a single session."""
    tag = _short(session_id)
    say = logger.info if quiet else (lambda msg: console.print(f"    {msg}"))

    # ── L0: Compress ──
    t0 = time.monotonic()
    say(f"[{tag}] L0 compress...")
    session = source.get_session(session_id)
    cleaned = preprocess(session)

    cfg = config or DistillConfig.load()
    if not should_process(cleaned, cfg.filter.min_messages, cfg.filter.min_tools):
        say(f"[{tag}] skipped: {cleaned.message_count} msgs, {cleaned.tool_count} tools")
        return None

    say(f"[{tag}] L0 done {_fmt_dur(time.monotonic()-t0)}: "
        f"{cleaned.message_count} msgs, {cleaned.tool_count} tools, "
        f"{len(cleaned.user_anchors)} anchors")

    # ── L1a: Intent boundaries ──
    t1 = time.monotonic()
    say(f"[{tag}] L1a detect boundaries...")
    blocks = detect_intent_boundaries(cleaned, fast_llm)
    say(f"[{tag}] L1a done {_fmt_dur(time.monotonic()-t1)}: {len(blocks)} blocks")

    # ── L1b: Per-block extraction (parallel when >1 block and >1 worker) ──
    t_l1b = time.monotonic()
    block_summaries = _extract_blocks(cleaned, blocks, fast_llm, tag, say, max_workers)
    say(f"[{tag}] L1b done {_fmt_dur(time.monotonic()-t_l1b)}: {len(block_summaries)} summaries")

    # ── L2: Aggregate ──
    t2 = time.monotonic()
    say(f"[{tag}] L2 aggregate...")
    trajectory = aggregate_session_summary(cleaned, block_summaries, fast_llm)
    say(f"[{tag}] L2 done {_fmt_dur(time.monotonic()-t2)}: "
        f"{trajectory.label} ({trajectory.label_score:.2f})")

    say(f"[{tag}] total {_fmt_dur(time.monotonic()-t0)}")
    return trajectory


def _extract_blocks(
    cleaned: CleanedSession,
    blocks: list,
    fast_llm: LLMClient,
    tag: str,
    say,
    max_workers: int,
) -> list:
    """Extract block summaries, parallelizing when beneficial."""
    if len(blocks) <= 1 or max_workers <= 1:
        # Sequential — better logging
        summaries = []
        for i, block in enumerate(blocks):
            say(f"[{tag}] L1b block {i+1}/{len(blocks)}: {block.intent[:50]}")
            summary = extract_block_summary(cleaned, block, fast_llm)
            summaries.append(summary)
        return summaries

    # Parallel block extraction
    say(f"[{tag}] L1b extracting {len(blocks)} blocks (parallel)...")
    summaries: list = [None] * len(blocks)
    with ThreadPoolExecutor(max_workers=min(max_workers, len(blocks))) as pool:
        futures = {}
        for i, block in enumerate(blocks):
            future = pool.submit(extract_block_summary, cleaned, block, fast_llm)
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                summaries[idx] = future.result()
            except Exception as e:
                logger.warning("Block %d failed: %s", idx, e)
                summaries[idx] = None

    return [s for s in summaries if s is not None]


def run_batch(
    session_ids: list[str],
    fast_llm: LLMClient,
    source: SessionSource,
    config: DistillConfig | None = None,
    *,
    max_workers: int = 1,
) -> list[TrajectorySummary]:
    """Run pipeline on multiple sessions, optionally in parallel."""
    results: list[TrajectorySummary] = []
    total = len(session_ids)

    if total <= 1:
        # Single session: enable block-level parallelism
        for idx, sid in enumerate(session_ids, 1):
            console.print(f"  [bold][{idx}/{total}][/] {sid}")
            try:
                result = run_pipeline(
                    sid, fast_llm, source, config,
                    quiet=False, max_workers=max_workers,
                )
                if result:
                    results.append(result)
            except Exception as e:
                logger.warning("Session %s failed: %s", sid, e)
                console.print(f"    [red]Failed: {e}[/]")
    else:
        # Multiple sessions: session-level parallelism, block-level sequential
        console.print(f"  Parallel preprocessing with {max_workers} workers...")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for sid in session_ids:
                future = pool.submit(
                    run_pipeline, sid, fast_llm, source, config,
                    quiet=True, max_workers=1,  # block-level serial to avoid overload
                )
                futures[future] = sid

            for future in as_completed(futures):
                sid = futures[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                    console.print(f"    [green]done[/]: {_short(sid)}")
                except Exception as e:
                    logger.warning("Session %s failed: %s", sid, e)
                    console.print(f"    [red]failed[/]: {_short(sid)}: {e}")

    return results
