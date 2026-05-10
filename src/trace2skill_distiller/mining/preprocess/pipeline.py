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

    # ── L1b: Per-block extraction ──
    block_summaries = []
    for i, block in enumerate(blocks):
        tb = time.monotonic()
        say(f"[{tag}] L1b block {i+1}/{len(blocks)}: {block.intent[:50]}")
        summary = extract_block_summary(cleaned, block, fast_llm)
        block_summaries.append(summary)
        say(f"[{tag}] L1b block {i+1} done {_fmt_dur(time.monotonic()-tb)}")

    # ── L2: Aggregate ──
    t2 = time.monotonic()
    say(f"[{tag}] L2 aggregate...")
    trajectory = aggregate_session_summary(cleaned, block_summaries, fast_llm)
    say(f"[{tag}] L2 done {_fmt_dur(time.monotonic()-t2)}: "
        f"{trajectory.label} ({trajectory.label_score:.2f})")

    say(f"[{tag}] total {_fmt_dur(time.monotonic()-t0)}")
    return trajectory


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
    # Always use sequential if <= 1 session — gives better logs and no overhead
    effective_workers = max_workers if total > 1 else 1

    if effective_workers <= 1:
        for idx, sid in enumerate(session_ids, 1):
            console.print(f"  [bold][{idx}/{total}][/] {sid}")
            try:
                result = run_pipeline(sid, fast_llm, source, config, quiet=False)
                if result:
                    results.append(result)
            except Exception as e:
                logger.warning("Session %s failed: %s", sid, e)
                console.print(f"    [red]Failed: {e}[/]")
    else:
        console.print(f"  Parallel preprocessing with {effective_workers} workers...")
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {}
            for sid in session_ids:
                future = pool.submit(
                    run_pipeline, sid, fast_llm, source, config, quiet=True,
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
