"""Trace2Skill Distiller CLI — thin shell delegating to orchestrator."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import click
from rich.panel import Panel

from ..core.config import DistillConfig, LLMConfig, init_default_config, set_config_value
from ..core.console import console
from ..llm import LLMClient
from ..llm.providers.openai_compatible import OpenAICompatibleProvider
from ..mining.mining_facade import DefaultMiningLayer
from ..mining.sources import create_source
from ..orchestrator.pipeline import DistillPipeline


def _load_config() -> DistillConfig:
    """Load config, ensuring .env is sourced if present."""
    env_file = Path.home() / ".trace2skill" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("TRACE2SKILL_"):
                    os.environ[key] = val.strip()
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
@click.option("--source", "source_type",
              type=click.Choice(["opencode", "chrys"], case_sensitive=False),
              default="opencode", help="数据源类型（Coding Agent）")
@click.option("--proxy", default="", help="代理地址（如 socks5://127.0.0.1:1080）")
@click.option("--proxy-bypass", default="", help="不走代理的 host 正则，逗号分隔（如 localhost,127\\.0\\.0\\.1）")
@click.option("--verify-ssl/--no-verify-ssl", default=False, help="是否验证 SSL 证书")
@click.option("--timeout", type=float, default=120.0, help="请求超时（秒）")
@click.option("--connect-timeout", type=float, default=10.0, help="连接超时（秒）")
def init(
    api_key: str, base_url: str, fast_model: str, strong_model: str,
    source_type: str,
    proxy: str, proxy_bypass: str, verify_ssl: bool,
    timeout: float, connect_timeout: float,
):
    """初始化 trace2skill 配置。

    创建 ~/.trace2skill/config.yaml 和 ~/.trace2skill/.env，
    写入 API 凭证。首次使用前运行一次即可。
    """
    path = init_default_config(
        api_key, base_url, fast_model, strong_model,
        proxy=proxy, proxy_bypass=proxy_bypass,
        verify_ssl=verify_ssl, timeout=timeout, connect_timeout=connect_timeout,
        source_type=source_type,
    )
    console.print(Panel(
        f"Config created: {path}\n"
        f"API key saved to: {path.parent / '.env'}\n"
        f"Fast model: {fast_model}\n"
        f"Strong model: {strong_model}\n"
        f"Source: {source_type}",
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

    source = create_source(config.source)
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


# ── config ──

@cli.group()
def config():
    """查看和管理配置。

    \b
    子命令:
      show   显示当前有效配置（API Key 脱敏）
      set    设置单个配置项（点分路径 key）
      edit   用默认编辑器打开 config.yaml
    """
    pass


def _mask(s: str | None, visible: int = 4) -> str:
    """Mask a string, showing only the first `visible` chars."""
    if not s:
        return "(not set)"
    if len(s) <= visible:
        return "*" * len(s)
    return s[:visible] + "*" * (len(s) - visible)


def _format_llm_panel(label: str, cfg: LLMConfig) -> Panel:
    """Build a Rich Panel for one LLMConfig."""
    return Panel(
        f"model: {cfg.model}\n"
        f"max_tokens: {cfg.max_tokens}\n"
        f"api_key: {_mask(cfg.api_key)}\n"
        f"base_url: {cfg.base_url}\n"
        f"verify_ssl: {cfg.verify_ssl}\n"
        f"proxy: {cfg.proxy or '(none)'}\n"
        f"proxy_bypass: {cfg.proxy_bypass or '(none)'}\n"
        f"timeout: {cfg.timeout}\n"
        f"connect_timeout: {cfg.connect_timeout}\n"
        f"extra_headers: {cfg.extra_headers or '(none)'}\n"
        f"user_agent: {cfg.user_agent}",
        title=label,
    )


@config.command("show")
def config_show():
    """显示当前有效配置（API Key 脱敏）。

    从 ~/.trace2skill/config.yaml 加载配置，展示 fast 和 strong
    模型的全部字段。环境变量覆盖也会反映在输出中。
    """
    cfg = _load_config()
    console.print(_format_llm_panel("Fast Model", cfg.fast_model))
    console.print()
    console.print(_format_llm_panel("Strong Model", cfg.strong_model))
    console.print()
    console.print(Panel(
        f"type: {cfg.source.type}\n"
        f"opencode.db_path: {cfg.source.opencode.db_path}\n"
        f"chrys.sessions_dir: {cfg.source.chrys.sessions_dir or '(auto-detect)'}",
        title="Source",
    ))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """设置单个配置项。

    KEY 使用点分路径格式，如 fast.proxy、strong.timeout。
    VALUE 为字符串值，会自动转换为正确的类型。

    \b
    示例:
      $ trace2skill config set fast.proxy socks5://127.0.0.1:1080
      $ trace2skill config set fast.proxy_bypass "localhost,127\\.0\\.0\\.1"
      $ trace2skill config set strong.timeout 180
      $ trace2skill config set fast.verify_ssl true
    """
    try:
        set_config_value(key, value)
        console.print(f"[green]Set {key} = {value}[/]")
    except ValueError as e:
        console.print(f"[red]{e}[/]")


@config.command("edit")
def config_edit():
    """用默认编辑器打开 config.yaml。

    使用 VISUAL 或 EDITOR 环境变量指定的编辑器。
    若未设置则回退到 notepad (Windows) 或 vi (Unix)。
    """
    import subprocess
    import platform

    config_path = DistillConfig.default_config_path()
    if not config_path.exists():
        console.print("[red]No config file found. Run 'trace2skill init' first.[/]")
        return

    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or ("notepad" if platform.system() == "Windows" else "vi")
    )
    console.print(f"Opening {config_path} with {editor}...")
    try:
        subprocess.call([editor, str(config_path)])
    except FileNotFoundError:
        console.print(f"[red]Editor '{editor}' not found. Set VISUAL or EDITOR env var.[/]")
    except OSError as e:
        console.print(f"[red]Failed to open editor: {e}[/]")


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
