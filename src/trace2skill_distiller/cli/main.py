"""Trace2Skill Distiller CLI."""

from __future__ import annotations

import json
import sys
import time
import uuid
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
from ..engine.cluster import cluster_by_topic
from ..engine.distill import distill_all_topics
from ..engine.merge import write_or_merge_topic, write_index, save_trajectories
from ..engine.report import generate_report
from ..models import (
    DistillReport, SessionEntry, TopicEntry, StepTiming, LLMUsage,
)

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
    """Trace2Skill Distiller — 从 OpenCode 会话轨迹中蒸馏可复用的技能。

    \b
    处理流水线:
      1. 预处理     — 噪声过滤 (L0) → 快速 LLM 摘要 (L1/L2)
      2. 主题聚类   — 按技术主题分组（快速 LLM）
      3. 按主题蒸馏 — 提取技能规则（强力 LLM）
      4. 写入       — 每个主题生成独立的 .md 技能文件

    \b
    快速上手:
      $ trace2skill init                         # 首次初始化（配置 API Key 和模型）
      $ trace2skill distill                      # 蒸馏全部会话
      $ trace2skill distill -p my-project        # 按项目过滤
      $ trace2skill distill -s SESSION_ID        # 指定单个会话
      $ trace2skill inspect SESSION_ID           # 预览预处理结果
      $ trace2skill status                       # 查看蒸馏历史

    \b
    配置文件: ~/.trace2skill/config.yaml
    输出目录: ~/.trace2skill/skills/<project>/<topic-id>/SKILL.md
    """
    pass


# ── init ──

@cli.command()
@click.option("--api-key", prompt="API Key", help="LLM API 密钥")
@click.option("--base-url", prompt="Base URL", help="LLM API 基础地址（如 https://api.openai.com/v1）")
@click.option("--fast-model", default="openai/gpt-oss-120b", help="快速模型（用于 L1/L2 预处理）")
@click.option("--strong-model", default="openai/gpt-oss-120b", help="强力模型（用于蒸馏和合并）")
def init(api_key: str, base_url: str, fast_model: str, strong_model: str):
    """初始化 trace2skill 配置。

    创建 ~/.trace2skill/config.yaml 和 ~/.trace2skill/.env，
    写入 API 凭证。首次使用前运行一次即可。
    """
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
@click.option("--project", "-p", help="按项目名称过滤（子串匹配）")
@click.option("--session", "-s", "session_id", help="指定要蒸馏的会话 ID")
@click.option("--from", "from_date", help="起始日期过滤（格式: YYYY-MM-DD）")
@click.option("--to", "to_date", help="截止日期过滤（格式: YYYY-MM-DD）")
@click.option("--step", type=int, help="执行到指定步骤后停止: 1=仅预处理, 2=蒸馏（不合并）")
@click.option("--dry-run", is_flag=True, help="仅展示蒸馏规则，不写入文件")
@click.option("--incremental", is_flag=True, help="仅处理上次运行之后的新会话")
def distill(
    project: str | None,
    session_id: str | None,
    from_date: str | None,
    to_date: str | None,
    step: int | None,
    dry_run: bool,
    incremental: bool,
):
    """从 OpenCode 会话轨迹中蒸馏可复用技能。

    \b
    完整流水线包含 4 个步骤:
      步骤 1  预处理   — 过滤噪声，用快速 LLM 生成摘要
      步骤 1.5 主题聚类 — 按技术主题对轨迹分组
      步骤 2  蒸馏     — 按主题提取技能规则（强力 LLM）
      步骤 3  写入     — 每个主题生成独立的 .md 技能文件

    使用 --step 可提前终止，--dry-run 可预览而不写入。
    会话需满足质量阈值（config 中 min_messages、min_tools）才会被处理。
    """
    config = _load_config()
    fast_llm = _make_fast_llm(config)
    strong_llm = _make_strong_llm(config)

    # Initialize report
    run_id = uuid.uuid4().hex[:8]
    project_name = project or "general"
    report = DistillReport(
        run_id=run_id,
        project=project_name,
        started_at=datetime.now().isoformat(),
    )
    run_start = time.monotonic()

    console.print(Panel(
        f"[bold]Trace2Skill Distiller v0.1[/]",
        subtitle=f"Project: {project or 'all'} | Run: {run_id}",
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

    report.sessions_total = len(sessions_meta)

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

    report.sessions_passed_filter = len(candidates)

    if not candidates:
        console.print("[yellow]No suitable sessions for distillation.[/]")
        _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)
        return

    # Step 1: Preprocessing pipeline
    console.print("\n[bold]Step 1: Preprocessing (Level 0 → 1 → 2)...[/]")
    step_start = time.monotonic()
    trajectories = run_batch(
        [s["id"] for s in candidates],
        fast_llm,
        config,
    )
    report.steps.append(StepTiming(
        name="Preprocessing (L0→L1→L2)",
        start=datetime.now().isoformat(),
        duration_seconds=time.monotonic() - step_start,
    ))

    # Collect session entries for report
    for t in trajectories:
        # Build brief reason for non-success sessions
        reason = ""
        if t.label != "success":
            parts = []
            if t.problems_encountered:
                first_problem = t.problems_encountered[0].problem
                parts.append(first_problem[:80])
            if t.lessons_learned:
                parts.append(t.lessons_learned[0][:80])
            reason = "；".join(parts) if parts else "综合评分不足"
        report.sessions.append(SessionEntry(
            session_id=t.session_id,
            project=t.project,
            label=t.label,
            label_score=t.label_score,
            intent=t.intent,
            msg_count=sum(1 for s in candidates if s["id"] == t.session_id),
            problems_count=len(t.problems_encountered),
            lessons_count=len(t.lessons_learned),
            label_reason=reason,
        ))

    console.print(
        f"\nPreprocessing complete: "
        f"T+={sum(1 for t in trajectories if t.label == 'success')} "
        f"T±={sum(1 for t in trajectories if t.label == 'partial')} "
        f"T-={sum(1 for t in trajectories if t.label == 'failure')}"
    )

    if not trajectories:
        console.print("[yellow]No trajectories passed preprocessing.[/]")
        _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)
        return

    if step == 1:
        output_dir = Path(config.skill_output_dir).expanduser()
        path = save_trajectories(trajectories, output_dir, project or "all")
        console.print(f"Trajectories saved to: {path}")
        _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)
        return

    # Step 1.5: Topic clustering
    console.print("\n[bold]Step 1.5: Clustering trajectories by topic...[/]")
    step_start = time.monotonic()
    output_dir = Path(config.skill_output_dir).expanduser()

    clustering = cluster_by_topic(
        trajectories,
        fast_llm,
        min_size=config.clustering_min_size,
        max_topics=config.clustering_max_topics,
        output_dir=output_dir,
        project=project_name,
        protected_topics=config.protected_topics or None,
    )
    report.steps.append(StepTiming(
        name="Topic Clustering",
        duration_seconds=time.monotonic() - step_start,
    ))
    report.topics_found = len(clustering.clusters)
    report.unclustered_count = len(clustering.unclustered)

    console.print(f"  Clustered into [green]{len(clustering.clusters)}[/] topics "
                  f"({len(clustering.unclustered)} unclustered)")
    for c in clustering.clusters:
        console.print(f"    - {c.topic_name} ({len(c.session_ids)} sessions)")

    # Step 2: Per-topic distillation
    console.print("\n[bold]Step 2: Distilling skill rules per topic...[/]")
    step_start = time.monotonic()
    skills = distill_all_topics(
        trajectories,
        clustering.clusters,
        strong_llm,
    )
    report.steps.append(StepTiming(
        name="Per-Topic Distillation",
        duration_seconds=time.monotonic() - step_start,
    ))

    total_rules = sum(len(s.rules) for s in skills)
    report.total_rules = total_rules
    console.print(f"\nDistilled {total_rules} rules across {len(skills)} topic skills")

    if not skills:
        console.print("[yellow]No rules distilled.[/]")
        _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)
        return

    # Collect topic entries for report
    for s in skills:
        report.topics.append(TopicEntry(
            topic_id=s.topic_id,
            topic_name=s.topic_name,
            topic_summary=s.summary,
            session_count=len(s.source_sessions),
            session_ids=s.source_sessions,
            rule_count=len(s.rules),
            skill_title=s.skill_title,
            description=s.description,
            rules=s.rules,
        ))

    if step == 2 or dry_run:
        for s in skills:
            console.print(f"\n[bold]Topic: {s.skill_title}[/] ({len(s.rules)} rules)")
            console.print(f"  {s.summary}")
            for r in s.rules:
                console.print(f"  [{r.type}] {r.action} (confidence: {r.confidence:.2f})")
        _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)
        return

    # Step 3: Write per-topic skill files
    console.print("\n[bold]Step 3: Writing topic skill files...[/]")
    step_start = time.monotonic()
    written_paths = []
    for skill in skills:
        path = write_or_merge_topic(
            skill,
            output_dir,
            project_name,
            fast_llm=fast_llm,
            max_rules=config.max_rules_per_skill,
        )
        written_paths.append(path)
        console.print(f"  Written: {path}")
        # Update report with output path
        for t in report.topics:
            if t.topic_id == skill.topic_id:
                t.output_path = str(path)

    report.steps.append(StepTiming(
        name="Write SKILL.md Files",
        duration_seconds=time.monotonic() - step_start,
    ))

    # Write index
    index_path = write_index(skills, output_dir, project_name)
    console.print(f"  Index: {index_path}")

    # Save trajectories
    save_trajectories(trajectories, output_dir, project_name)

    # Update state
    _save_state(trajectories, project)

    console.print(Panel(
        f"Sessions analyzed: {len(trajectories)} "
        f"(T+={sum(1 for t in trajectories if t.label == 'success')}, "
        f"T-={sum(1 for t in trajectories if t.label != 'success')})\n"
        f"Topics discovered: {len(clustering.clusters)}\n"
        f"Skills written: {len(written_paths)}\n"
        f"Total rules: {total_rules}\n"
        f"Output dir: {output_dir / project_name}/",
        title="Distillation Complete",
    ))

    # Finalize report
    _finalize_report(report, run_start, fast_llm, strong_llm, config, project_name)


# ── inspect ──

@cli.command()
@click.argument("session_id")
def inspect(session_id: str):
    """查看单个会话的预处理结果。

    对指定会话运行 L0→L1→L2 预处理流水线，展示生成的轨迹摘要:
    类型、意图、阶段、遇到的问题及经验教训。
    适用于调试过滤阈值。

    \b
    示例:
      $ trace2skill inspect abc123-def456
    """
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
    """查看蒸馏状态和历史。

    显示上次运行时间、已处理的会话数、累计消耗费用，
    并列出所有已生成的 SKILL.md 文件。
    """
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
        # Find SKILL.md files inside topic directories
        skills = list(skill_dir.rglob("*/SKILL.md"))
        if skills:
            console.print(f"\n[bold]Topic skills ({len(skills)}):[/]")
            for s in skills:
                size = s.stat().st_size
                console.print(f"  {s.relative_to(skill_dir)} ({size} bytes)")


# ── schedule ──

@cli.group()
def schedule():
    """管理定时蒸馏任务。

    \b
    子命令:
      start   启动调度守护进程（前台运行）
      stop    提示如何停止（Ctrl+C 终止进程）
      status  查看调度器配置和状态
    """
    pass


@schedule.command("start")
def schedule_start():
    """启动定时蒸馏守护进程。

    在前台运行，按照 config.yaml 中 scheduler.cron 定义的时间触发蒸馏。
    需要先在配置中设置 scheduler.enabled=true。
    按 Ctrl+C 停止。
    """
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
    """停止正在运行的调度守护进程。

    调度器在前台运行，需在其终端按 Ctrl+C 停止。
    此命令仅作为提醒。
    """
    console.print("[yellow]Manual stop — kill the running process (Ctrl+C).[/]")


@schedule.command("status")
def schedule_status():
    """查看调度器配置及启用状态。

    从 config.yaml 读取调度器设置: 是否启用、cron 表达式、增量策略。
    """
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

    output_dir = Path(config.skill_output_dir).expanduser()
    clustering = cluster_by_topic(
        trajectories, fast_llm,
        min_size=config.clustering_min_size,
        max_topics=config.clustering_max_topics,
        output_dir=output_dir,
        project="scheduled",
        protected_topics=config.protected_topics or None,
    )

    skills = distill_all_topics(trajectories, clustering.clusters, strong_llm)

    if skills:
        for skill in skills:
            write_or_merge_topic(
                skill, output_dir, "scheduled",
                fast_llm=fast_llm,
                max_rules=config.max_rules_per_skill,
            )
        write_index(skills, output_dir, "scheduled")
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


def _finalize_report(
    report: DistillReport,
    run_start: float,
    fast_llm: LLMClient,
    strong_llm: LLMClient | None,
    config: DistillConfig,
    project_name: str,
):
    """Finalize report data and write HTML."""
    report.finished_at = datetime.now().isoformat()
    report.total_duration_seconds = time.monotonic() - run_start
    report.output_dir = str(Path(config.skill_output_dir).expanduser() / project_name)

    # Collect LLM stats
    fast_stats = fast_llm.reset_stats()
    report.llm_usage.append(LLMUsage(
        label=f"fast ({config.fast_model.model})",
        calls=fast_stats["calls"],
        input_tokens=fast_stats["input_tokens"],
        output_tokens=fast_stats["output_tokens"],
    ))
    if strong_llm:
        strong_stats = strong_llm.reset_stats()
        report.llm_usage.append(LLMUsage(
            label=f"strong ({config.strong_model.model})",
            calls=strong_stats["calls"],
            input_tokens=strong_stats["input_tokens"],
            output_tokens=strong_stats["output_tokens"],
        ))

    # Write HTML report
    report_dir = Path.home() / ".trace2skill" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{report.run_id}.html"
    generate_report(report, report_path)
    console.print(f"\n[bold green]Report:[/] {report_path}")


if __name__ == "__main__":
    cli()
