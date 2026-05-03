"""Trace2Skill Distiller CLI — thin shell delegating to orchestrator."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from ..core.config import DistillConfig, init_default_config
from ..llm import LLMClient
from ..llm.providers.openai_compatible import OpenAICompatibleProvider
from ..mining.mining_facade import DefaultMiningLayer
from ..mining.sources.opencode import OpenCodeSource
from ..orchestrator.pipeline import DistillPipeline

import sys as _sys
_console_file = open(_sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
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
    """
    config = _load_config()
    pipeline = DistillPipeline.from_config(config)

    # Handle incremental
    since_ts = None
    if incremental:
        from ..output.state import StateManager
        state_mgr = StateManager()
        since_ts = state_mgr.get_last_run_ts()

    pipeline.run(
        project=project,
        session_id=session_id,
        since=since_ts,
        step=step,
        dry_run=dry_run,
    )


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
    fast_provider = OpenAICompatibleProvider(config.fast_model)
    fast_llm = LLMClient(fast_provider)

    source = OpenCodeSource(config.opencode.db_path, config.opencode.export_command)
    from ..mining.preprocess.pipeline import run_pipeline

    console.print(f"Inspecting session [cyan]{session_id}[/]...")

    try:
        result = run_pipeline(session_id, fast_llm, source, config)
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

    # Print LLM stats
    fast_stats = fast_llm.reset_stats()
    console.print(f"\n[dim]Fast LLM: {fast_stats['calls']} calls, "
                  f"{fast_stats['input_tokens']}+{fast_stats['output_tokens']} tokens[/]")


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

    config = _load_config()
    if not config.scheduler.enabled:
        console.print("[yellow]Scheduler is not enabled in config. Set scheduler.enabled = true[/]")
        return

    cron = config.scheduler.cron
    console.print(f"Starting scheduler with cron: {cron}")
    console.print("[dim]Press Ctrl+C to stop[/]")

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
    pipeline = DistillPipeline.from_config(config)

    now = datetime.now().isoformat()
    console.print(f"[{now}] Scheduled distillation starting...")

    from ..output.state import StateManager
    state_mgr = StateManager()
    since_ts = state_mgr.get_last_run_ts()

    pipeline.run(
        project=None,
        since=since_ts,
    )


if __name__ == "__main__":
    cli()
