"""Trace2Skill Distiller CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import DistillConfig, init_default_config
from ..llm import LLMClient
from ..db import list_sessions, count_tools, export_session
from ..pipeline import run_pipeline, run_batch
from ..engine.distill import distill_all_dimensions
from ..engine.merge import merge_and_write, save_trajectories

import sys
_console_file = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = Console(file=_console_file)


def _load_config() -> DistillConfig:
    """Load config, ensuring .env is sourced if present."""
    env_file = Path.home() / ".trace2skill" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                import os
                os.environ[key.strip()] = val.strip()
    return DistillConfig.load()


def _make_fast_llm(config: DistillConfig) -> LLMClient:
    return LLMClient(config.fast_model)


def _make_strong_llm(config: DistillConfig) -> LLMClient:
    return LLMClient(config.strong_model)


@click.group()
@click.version_option("0.1.0")
def cli():
    """Trace2Skill Distiller — distill reusable skills from OpenCode session traces."""
    pass


# ── init ──

@cli.command()
@click.option("--api-key", prompt="API Key", help="LLM API key")
@click.option("--base-url", prompt="Base URL", help="LLM API base URL")
@click.option("--fast-model", default="openai/gpt-oss-120b", help="Fast model ID")
@click.option("--strong-model", default="openai/gpt-oss-120b", help="Strong model ID")
def init(api_key: str, base_url: str, fast_model: str, strong_model: str):
    """Initialize trace2skill configuration."""
    path = init_default_config(api_key, base_url, fast_model, strong_model)
    console.print(Panel(
        f"Config created: {path}\n"
        f"API key saved to: {path.parent / '.env'}\n"
        f"Fast model: {fast_model}\n"
        f"Strong model: {strong_model}",
        title="Trace2Skill Initialized",
    ))


# ── distill ──

@cli.command()
@click.option("--project", "-p", help="Filter by project name")
@click.option("--session", "-s", "session_id", help="Distill a specific session")
@click.option("--from", "from_date", help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", help="End date (YYYY-MM-DD)")
@click.option("--step", type=int, help="Only run up to this step (1=preprocess, 2=distill)")
@click.option("--dry-run", is_flag=True, help="Don't write files, just show analysis")
@click.option("--incremental", is_flag=True, help="Only process new sessions")
def distill(
    project: str | None,
    session_id: str | None,
    from_date: str | None,
    to_date: str | None,
    step: int | None,
    dry_run: bool,
    incremental: bool,
):
    """Distill skills from OpenCode sessions."""
    config = _load_config()
    fast_llm = _make_fast_llm(config)
    strong_llm = _make_strong_llm(config)

    console.print(Panel(
        f"[bold]Trace2Skill Distiller v0.1[/]",
        subtitle=f"Project: {project or 'all'}",
    ))

    # Get sessions
    since_ts = None
    if incremental:
        state_file = Path.home() / ".trace2skill" / "state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            last = state.get("last_run", "")
            if last:
                dt = datetime.fromisoformat(last)
                since_ts = int(dt.timestamp() * 1000)

    if session_id:
        sessions_meta = [{"id": session_id, "title": "specified session", "msg_count": 999}]
    else:
        sessions_meta = list_sessions(config, project=project, since=since_ts)

    if not sessions_meta:
        console.print("[yellow]No sessions found.[/]")
        return

    # Display candidates
    table = Table(title="Session Candidates")
    table.add_column("#", width=3)
    table.add_column("Session ID", width=20)
    table.add_column("Title", width=40)
    table.add_column("Msgs", width=5)
    table.add_column("Tools", width=5)

    # Pre-filter with tool counts
    candidates = []
    for s in sessions_meta:
        tc = count_tools(s["id"], config) if not session_id else 0
        mc = s.get("msg_count", 0)
        if mc >= config.filter.min_messages and (session_id or tc >= config.filter.min_tools):
            candidates.append(s)
            table.add_row(
                str(len(candidates)),
                s["id"][:20],
                (s.get("title") or "")[:40],
                str(mc),
                str(tc),
            )

    console.print(table)
    console.print(f"\n[green]{len(candidates)}[/] sessions pass quality threshold "
                  f"(out of {len(sessions_meta)} total)")

    if not candidates:
        console.print("[yellow]No suitable sessions for distillation.[/]")
        return

    # Step 1: Preprocessing pipeline
    console.print("\n[bold]Step 1: Preprocessing (Level 0 → 1 → 2)...[/]")
    trajectories = run_batch(
        [s["id"] for s in candidates],
        fast_llm,
        config,
    )

    console.print(
        f"\nPreprocessing complete: "
        f"T+={sum(1 for t in trajectories if t.label == 'success')} "
        f"T±={sum(1 for t in trajectories if t.label == 'partial')} "
        f"T-={sum(1 for t in trajectories if t.label == 'failure')}"
    )

    if not trajectories:
        console.print("[yellow]No trajectories passed preprocessing.[/]")
        return

    if step == 1:
        # Save trajectories and stop
        output_dir = Path(config.skill_output_dir).expanduser()
        path = save_trajectories(trajectories, output_dir, project or "all")
        console.print(f"Trajectories saved to: {path}")
        _print_stats(fast_llm, strong_llm)
        return

    # Step 2: Distillation
    console.print("\n[bold]Step 2: Distilling skill rules...[/]")
    patches = distill_all_dimensions(
        trajectories,
        project or "general",
        strong_llm,
    )

    total_rules = sum(len(p.rules) for p in patches)
    console.print(f"\nDistilled {total_rules} candidate rules across {len(patches)} dimensions")

    if not patches:
        console.print("[yellow]No rules distilled.[/]")
        _print_stats(fast_llm, strong_llm)
        return

    if step == 2 or dry_run:
        for p in patches:
            console.print(f"\n[bold]Dimension: {p.dimension}[/]")
            for r in p.rules:
                console.print(f"  [{r.type}] {r.action} (confidence: {r.confidence:.2f})")
        _print_stats(fast_llm, strong_llm)
        return

    # Step 3: Merge and write
    console.print("\n[bold]Step 3: Merging into SKILL.md...[/]")
    output_dir = Path(config.skill_output_dir).expanduser()
    skill_path = merge_and_write(
        patches,
        project or "general",
        output_dir,
        fast_llm,
        max_rules=config.max_rules_per_skill,
    )

    # Save trajectories too
    save_trajectories(trajectories, output_dir, project or "general")

    # Update state
    _save_state(trajectories, project)

    console.print(Panel(
        f"Sessions analyzed: {len(trajectories)} "
        f"(T+={sum(1 for t in trajectories if t.label == 'success')}, "
        f"T-={sum(1 for t in trajectories if t.label != 'success')})\n"
        f"Rules distilled: {total_rules}\n"
        f"Output: {skill_path}",
        title="Distillation Complete",
    ))

    _print_stats(fast_llm, strong_llm)


# ── inspect ──

@cli.command()
@click.argument("session_id")
def inspect(session_id: str):
    """Inspect a single session's preprocessing output."""
    config = _load_config()
    fast_llm = _make_fast_llm(config)

    console.print(f"Inspecting session [cyan]{session_id}[/]...")

    try:
        result = run_pipeline(session_id, fast_llm, config)
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")
        return

    if not result:
        console.print("[yellow]Session did not pass quality filter.[/]")
        return

    console.print(Panel(
        f"Type: {result.session_type}\n"
        f"Project: {result.project}\n"
        f"Intent: {result.intent}\n"
        f"Label: [green]{result.label}[/] (score: {result.label_score:.2f})",
        title=f"Session: {session_id}",
    ))

    if result.what_happened:
        console.print("\n[bold]Phases:[/]")
        for phase in result.what_happened:
            console.print(f"  {phase.phase}: {phase.summary}")

    if result.problems_encountered:
        console.print("\n[bold]Problems:[/]")
        for p in result.problems_encountered:
            console.print(f"  - {p.problem} → {p.how_resolved}")

    if result.lessons_learned:
        console.print("\n[bold]Lessons:[/]")
        for lesson in result.lessons_learned:
            console.print(f"  - {lesson}")

    _print_stats(fast_llm, None)


# ── status ──

@cli.command()
def status():
    """Show distillation status and history."""
    state_file = Path.home() / ".trace2skill" / "state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        console.print(Panel(
            f"Last run: {state.get('last_run', 'never')}\n"
            f"Sessions processed: {len(state.get('processed_sessions', []))}\n"
            f"Cost accumulated: ¥{state.get('cost_accumulated', 0):.4f}",
            title="Trace2Skill Status",
        ))
    else:
        console.print("[yellow]No distillation history found.[/]")

    # Show skill files
    skill_dir = Path.home() / ".trace2skill" / "skills"
    if skill_dir.exists():
        skills = list(skill_dir.rglob("SKILL.md"))
        if skills:
            console.print(f"\n[bold]Skill files ({len(skills)}):[/]")
            for s in skills:
                size = s.stat().st_size
                console.print(f"  {s.relative_to(skill_dir)} ({size} bytes)")


# ── schedule ──

@cli.group()
def schedule():
    """Manage scheduled distillation tasks."""
    pass


@schedule.command("start")
def schedule_start():
    """Start scheduled distillation daemon."""
    import schedule as sched_mod
    import time

    config = _load_config()
    if not config.scheduler.enabled:
        console.print("[yellow]Scheduler is not enabled in config. Set scheduler.enabled = true[/]")
        return

    cron = config.scheduler.cron
    console.print(f"Starting scheduler with cron: {cron}")
    console.print("[dim]Press Ctrl+C to stop[/]")

    # Simple implementation: parse cron for daily at HH:MM
    parts = cron.split()
    if len(parts) >= 2:
        minute, hour = parts[0], parts[1]
        time_str = f"{hour}:{minute}"
        sched_mod.every().day.at(time_str).do(_scheduled_run)

    try:
        while True:
            sched_mod.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler stopped.[/]")


@schedule.command("stop")
def schedule_stop():
    """Stop scheduled distillation daemon."""
    console.print("[yellow]Manual stop — kill the running process (Ctrl+C).[/]")


@schedule.command("status")
def schedule_status():
    """Show scheduler status."""
    config = _load_config()
    if config.scheduler.enabled:
        console.print(f"Scheduler: [green]enabled[/]")
        console.print(f"Cron: {config.scheduler.cron}")
        console.print(f"Strategy: {config.scheduler.strategy}")
    else:
        console.print("Scheduler: [yellow]disabled[/]")


# ── Helpers ──

def _scheduled_run():
    """Callback for scheduled runs."""
    config = _load_config()
    fast_llm = _make_fast_llm(config)
    strong_llm = _make_strong_llm(config)

    now = datetime.now().isoformat()
    console.print(f"[{now}] Scheduled distillation starting...")

    sessions_meta = list_sessions(config, since=_last_run_ts())
    if len(sessions_meta) < config.scheduler.min_new_sessions:
        console.print(f"Only {len(sessions_meta)} new sessions, below threshold. Skipping.")
        return

    # Run full pipeline
    candidates = [
        s for s in sessions_meta
        if s.get("msg_count", 0) >= config.filter.min_messages
    ]

    trajectories = run_batch([s["id"] for s in candidates], fast_llm, config)
    if not trajectories:
        return

    patches = distill_all_dimensions(trajectories, "scheduled", strong_llm)

    if patches:
        output_dir = Path(config.skill_output_dir).expanduser()
        merge_and_write(patches, "scheduled", output_dir, fast_llm)
        save_trajectories(trajectories, output_dir, "scheduled")

    _save_state(trajectories, None)


def _last_run_ts() -> int | None:
    state_file = Path.home() / ".trace2skill" / "state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        last = state.get("last_run", "")
        if last:
            dt = datetime.fromisoformat(last)
            return int(dt.timestamp() * 1000)
    return None


def _save_state(trajectories, project: str | None):
    state_file = Path.home() / ".trace2skill" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))

    state.update({
        "last_run": datetime.now().isoformat(),
        "processed_sessions": list(set(
            state.get("processed_sessions", [])
            + [t.session_id for t in trajectories]
        )),
        "stats": {
            "total_processed": len(state.get("processed_sessions", [])) + len(trajectories),
        },
    })

    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_stats(fast_llm: LLMClient, strong_llm: LLMClient | None):
    fast_stats = fast_llm.reset_stats()
    console.print(f"\n[dim]Fast LLM: {fast_stats['calls']} calls, "
                  f"{fast_stats['input_tokens']}+{fast_stats['output_tokens']} tokens[/]")
    if strong_llm:
        strong_stats = strong_llm.reset_stats()
        console.print(f"[dim]Strong LLM: {strong_stats['calls']} calls, "
                      f"{strong_stats['input_tokens']}+{strong_stats['output_tokens']} tokens[/]")


if __name__ == "__main__":
    cli()
