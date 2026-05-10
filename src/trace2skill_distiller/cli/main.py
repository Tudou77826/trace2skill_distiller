"""Trace2Skill Distiller CLI."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from ..core.config import DistillConfig, LLMConfig, init_default_config, set_config_value
from ..core.console import console
from ..llm import LLMClient
from ..llm.providers.openai_compatible import OpenAICompatibleProvider
from ..mining.sources import create_source
from ..orchestrator.pipeline import DistillPipeline
from ..output.types import DistillReport


def _setup_logging() -> None:
    """Configure package-level logging to stderr."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


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


def _mask(s: str | None, visible: int = 4) -> str:
    """Mask a string, showing only the first `visible` chars."""
    if not s:
        return "(not set)"
    if len(s) <= visible:
        return "*" * len(s)
    return s[:visible] + "*" * (len(s) - visible)


def _format_llm_panel(label: str, cfg: LLMConfig) -> Panel:
    """Build a Rich panel for one model config."""
    return Panel(
        f"model: {cfg.model}\n"
        f"max_tokens: {cfg.max_tokens}\n"
        f"max_concurrency: {cfg.max_concurrency}\n"
        f"max_rpm: {cfg.max_rpm}\n"
        f"api_key: {_mask(cfg.api_key)}\n"
        f"base_url: {cfg.base_url or '(not set)'}\n"
        f"verify_ssl: {cfg.verify_ssl}\n"
        f"proxy: {cfg.proxy or '(none)'}\n"
        f"proxy_bypass: {cfg.proxy_bypass or '(none)'}\n"
        f"timeout: {cfg.timeout}\n"
        f"connect_timeout: {cfg.connect_timeout}\n"
        f"extra_headers: {cfg.extra_headers or '(none)'}\n"
        f"user_agent: {cfg.user_agent}",
        title=label,
    )


def _current_source_location(cfg: DistillConfig) -> str:
    """Return the active source location for display."""
    if cfg.source.type == "chrys":
        return cfg.source.chrys.sessions_dir or "(auto-detect)"
    return cfg.source.opencode.db_path


def _report_dir() -> Path:
    return Path.home() / ".trace2skill" / "reports"


def _load_report(run_id: str) -> tuple[Path, DistillReport] | None:
    """Load a JSON report by run id."""
    path = _report_dir() / f"{run_id}.json"
    if not path.exists():
        return None
    return path, DistillReport.model_validate_json(path.read_text(encoding="utf-8"))


def _iter_reports() -> list[tuple[Path, DistillReport]]:
    """Load all JSON reports, newest first."""
    report_dir = _report_dir()
    if not report_dir.exists():
        return []

    reports: list[tuple[Path, DistillReport]] = []
    for path in sorted(report_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            reports.append((path, DistillReport.model_validate_json(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return reports


def _resolve_output_format(output_format: str) -> str:
    """Map user-facing output choices to internal formatter ids."""
    return "knowledge_md" if output_format == "knowledge" else "skill_md"


def _display_output_format(output_format: str) -> str:
    """Map internal formatter ids to user-facing labels."""
    return "knowledge" if output_format == "knowledge_md" else "skill"


def _fmt_timestamp(ts: int) -> str:
    """Format source timestamps safely."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


@click.group()
@click.version_option("0.1.0")
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志（INFO 级别）")
def cli(verbose: bool):
    """Trace2Skill Distiller。

    \b
    常用流程:
      $ trace2skill init
      $ trace2skill config show
      $ trace2skill sessions list
      $ trace2skill inspect session <SESSION_ID>
      $ trace2skill run --project my-project
      $ trace2skill runs list

    \b
    当前数据来源、模型级并发限制等都来自配置文件。
    `project` 和 `session` 只在当前 source 范围内解释，不会跨多种 coding 软件遍历。
    """
    _setup_logging()
    if verbose:
        logging.getLogger("trace2skill_distiller").setLevel(logging.INFO)


@cli.command()
@click.option("--api-key", prompt="API Key", help="LLM API 密钥")
@click.option("--base-url", prompt="Base URL", help="LLM API 基础地址（如 https://api.openai.com/v1）")
@click.option("--fast-model", default="openai/gpt-oss-120b", help="快速模型（用于预处理）")
@click.option("--strong-model", default="openai/gpt-oss-120b", help="强力模型（用于蒸馏）")
@click.option("--proxy", default="", help="代理地址（如 socks5://127.0.0.1:1080）")
@click.option("--proxy-bypass", default="", help="不走代理的 host 正则，逗号分隔")
@click.option("--verify-ssl/--no-verify-ssl", default=False, help="是否验证 SSL 证书")
@click.option("--timeout", type=float, default=120.0, help="请求超时（秒）")
@click.option("--connect-timeout", type=float, default=10.0, help="连接超时（秒）")
def init(
    api_key: str,
    base_url: str,
    fast_model: str,
    strong_model: str,
    proxy: str,
    proxy_bypass: str,
    verify_ssl: bool,
    timeout: float,
    connect_timeout: float,
):
    """初始化配置并写入默认运行设置。"""
    path = init_default_config(
        api_key,
        base_url,
        fast_model,
        strong_model,
        proxy=proxy,
        proxy_bypass=proxy_bypass,
        verify_ssl=verify_ssl,
        timeout=timeout,
        connect_timeout=connect_timeout,
    )
    console.print(Panel(
        f"Config created: {path}\n"
        f"API key saved to: {path.parent / '.env'}\n"
        f"Fast model: {fast_model}\n"
        f"Strong model: {strong_model}\n"
        f"Source: opencode (default)\n"
        f"Tip: use 'trace2skill config set source.type chrys' to switch source later.",
        title="Trace2Skill Initialized",
    ))


@cli.command()
def doctor():
    """检查当前配置、数据来源和模型基础设置。"""
    config_path = DistillConfig.default_config_path()
    env_path = config_path.parent / ".env"
    cfg = _load_config()

    checks: list[tuple[str, str, str]] = []
    checks.append(("config.yaml", "ok" if config_path.exists() else "missing", str(config_path)))
    checks.append((".env", "ok" if env_path.exists() else "missing", str(env_path)))
    checks.append(("source.type", "ok", cfg.source.type))

    source_location = _current_source_location(cfg)
    source_path_status = "auto"
    if source_location != "(auto-detect)":
        source_path = Path(source_location).expanduser()
        source_path_status = "ok" if source_path.exists() else "missing"
        source_location = str(source_path)
    checks.append(("source.path", source_path_status, source_location))

    checks.append(("fast_model.api_key", "ok" if cfg.fast_model.api_key else "missing", _mask(cfg.fast_model.api_key)))
    checks.append(("fast_model.base_url", "ok" if cfg.fast_model.base_url else "missing", cfg.fast_model.base_url or "(not set)"))
    checks.append(("strong_model.model", "ok" if cfg.strong_model.model else "missing", cfg.strong_model.model or "(not set)"))
    checks.append(("fast_model.max_concurrency", "ok", str(cfg.fast_model.max_concurrency)))
    checks.append(("strong_model.max_concurrency", "ok", str(cfg.strong_model.max_concurrency)))
    checks.append(("output.format", "ok", _display_output_format(cfg.output.format)))

    try:
        create_source(cfg.source)
        checks.append(("source.adapter", "ok", "source adapter created successfully"))
    except Exception as exc:
        checks.append(("source.adapter", "error", str(exc)))

    table = Table(title="Trace2Skill Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status", width=10)
    table.add_column("Details")

    has_error = False
    for name, status, details in checks:
        color = {
            "ok": "green",
            "missing": "red",
            "error": "red",
            "auto": "yellow",
        }.get(status, "white")
        if status in {"missing", "error"}:
            has_error = True
        table.add_row(name, f"[{color}]{status}[/]", details)

    console.print(table)
    if has_error:
        console.print("\n[red]Doctor found blocking issues. Fix configuration before running `trace2skill run`.[/]")
    else:
        console.print("\n[green]Doctor checks passed.[/]")


@cli.group()
def config():
    """查看和管理当前运行配置。"""


@config.command("show")
def config_show():
    """显示当前有效配置（API Key 脱敏）。"""
    cfg = _load_config()
    console.print(_format_llm_panel("Fast Model", cfg.fast_model))
    console.print()
    console.print(_format_llm_panel("Strong Model", cfg.strong_model))
    console.print()
    console.print(Panel(
        f"type: {cfg.source.type}\n"
        f"location: {_current_source_location(cfg)}",
        title="Current Source",
    ))
    console.print()
    console.print(Panel(
        f"output.format: {_display_output_format(cfg.output.format)}\n"
        f"output.skill_output_dir: {cfg.output.skill_output_dir}\n"
        f"filter.min_messages: {cfg.filter.min_messages}\n"
        f"filter.min_tools: {cfg.filter.min_tools}\n"
        f"analysis.clustering_max_topics: {cfg.analysis.clustering_max_topics}",
        title="Runtime Settings",
    ))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """设置单个配置项。"""
    try:
        normalized = _resolve_output_format(value.lower()) if key == "output.format" else value
        set_config_value(key, normalized)
        shown = _display_output_format(normalized) if key == "output.format" else normalized
        console.print(f"[green]Set {key} = {shown}[/]")
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")


@config.command("edit")
def config_edit():
    """用默认编辑器打开 config.yaml。"""
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
    except OSError as exc:
        console.print(f"[red]Failed to open editor: {exc}[/]")


@cli.group()
def sessions():
    """查看当前 source 下的会话。"""


@sessions.command("list")
@click.option("--project", "-p", help="按项目名称过滤（子串匹配）")
@click.option("--limit", type=int, default=20, show_default=True, help="最多显示 N 个会话")
@click.option("--include-low-quality", is_flag=True, help="包含未通过质量阈值的会话")
def sessions_list(project: str | None, limit: int, include_low_quality: bool):
    """列出当前 source 下的会话元数据。"""
    cfg = _load_config()
    source = create_source(cfg.source)
    sessions_meta = source.list_sessions(project=project)
    if not sessions_meta:
        console.print("[yellow]No sessions found in the current source.[/]")
        return

    for s in sessions_meta:
        s.tool_count = source.count_tools(s.id)

    filtered = sessions_meta
    if not include_low_quality:
        filtered = [
            s for s in sessions_meta
            if s.msg_count >= cfg.filter.min_messages and s.tool_count >= cfg.filter.min_tools
        ]

    if not filtered:
        console.print(
            f"[yellow]No sessions pass quality threshold (min {cfg.filter.min_messages} msgs, "
            f"{cfg.filter.min_tools} tools). Use --include-low-quality to inspect all.[/]"
        )
        return

    filtered.sort(key=lambda s: (s.timestamp, s.msg_count, s.tool_count), reverse=True)
    displayed = filtered[:limit]

    table = Table(title=f"Sessions | source={cfg.source.type} | showing {len(displayed)}/{len(filtered)}")
    table.add_column("#", width=3, style="dim")
    table.add_column("Session ID", width=20)
    table.add_column("Title", width=40)
    table.add_column("Project", width=18)
    table.add_column("Msgs", width=6, justify="right")
    table.add_column("Tools", width=6, justify="right")
    table.add_column("Date", width=12)

    for i, s in enumerate(displayed, start=1):
        table.add_row(
            str(i),
            s.id[:20],
            (s.title or "")[:40],
            (s.project or "")[:18],
            str(s.msg_count),
            str(s.tool_count),
            _fmt_timestamp(s.timestamp),
        )

    console.print(table)
    console.print("\n[dim]Use 'trace2skill sessions show <ID>' or 'trace2skill inspect session <ID>' for details.[/]")


@sessions.command("show")
@click.argument("session_id")
def sessions_show(session_id: str):
    """查看单个会话的元信息。"""
    cfg = _load_config()
    source = create_source(cfg.source)
    sessions_meta = source.list_sessions()
    meta = next((s for s in sessions_meta if s.id == session_id), None)

    if meta is None:
        session = source.get_session(session_id)
        if session is None:
            console.print(f"[red]Session not found: {session_id}[/]")
            return
        tool_count = session.tool_count
        msg_count = len(session.messages)
        project = session.project_name
        title = session.info.title
        timestamp = session.info.time.get("created", 0) // 1000 if session.info.time.get("created") else 0
    else:
        tool_count = source.count_tools(session_id)
        msg_count = meta.msg_count
        project = meta.project
        title = meta.title
        timestamp = meta.timestamp

    passes = msg_count >= cfg.filter.min_messages and tool_count >= cfg.filter.min_tools
    console.print(Panel(
        f"Source: {cfg.source.type}\n"
        f"Session ID: {session_id}\n"
        f"Title: {title or '(untitled)'}\n"
        f"Project: {project or '(unknown)'}\n"
        f"Date: {_fmt_timestamp(timestamp) or '(unknown)'}\n"
        f"Messages: {msg_count}\n"
        f"Tools: {tool_count}\n"
        f"Passes quality threshold: {'yes' if passes else 'no'}",
        title="Session Details",
    ))
    console.print("[dim]Next: inspect preprocessing with `trace2skill inspect session <ID>` or run distillation with `trace2skill run --session <ID>`.[/]")


@cli.group()
def inspect():
    """查看会话预处理结果或历史运行详情。"""


@inspect.command("session")
@click.argument("session_id")
def inspect_session(session_id: str):
    """查看单个会话的预处理结果。"""
    cfg = _load_config()
    fast_provider = OpenAICompatibleProvider(cfg.fast_model)
    fast_llm = LLMClient(fast_provider)
    source = create_source(cfg.source)

    from ..mining.preprocess.pipeline import run_pipeline

    console.print(f"Inspecting session [cyan]{session_id}[/] from source [cyan]{cfg.source.type}[/]...")
    try:
        result = run_pipeline(session_id, fast_llm, source, cfg)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/]")
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
        for problem in result.problems_encountered:
            console.print(f"  - {problem.problem} -> {problem.how_resolved}")

    if result.lessons_learned:
        console.print("\n[bold]Lessons:[/]")
        for lesson in result.lessons_learned:
            console.print(f"  - {lesson}")

    if result.discoveries:
        console.print("\n[bold]Discoveries:[/]")
        for discovery in result.discoveries:
            console.print(f"  - {discovery}")

    stats = fast_llm.reset_stats()
    console.print(
        f"\n[dim]Fast LLM: {stats['calls']} calls, "
        f"{stats['input_tokens']}+{stats['output_tokens']} tokens[/]"
    )


@inspect.command("run")
@click.argument("run_id")
def inspect_run(run_id: str):
    """查看单次运行的报告摘要。"""
    loaded = _load_report(run_id)
    if not loaded:
        console.print(f"[red]Run report not found: {run_id}[/]")
        return

    report_path, report = loaded
    html_path = report_path.with_suffix(".html")
    console.print(Panel(
        f"Run ID: {report.run_id}\n"
        f"Project: {report.project}\n"
        f"Started: {report.started_at}\n"
        f"Finished: {report.finished_at}\n"
        f"Duration: {report.total_duration_seconds:.1f}s\n"
        f"Sessions: {report.sessions_passed_filter}/{report.sessions_total}\n"
        f"Topics: {report.topics_found}\n"
        f"Rules: {report.total_rules}\n"
        f"Output dir: {report.output_dir or '(none)'}\n"
        f"JSON report: {report_path}\n"
        f"HTML report: {html_path if html_path.exists() else '(missing)'}",
        title="Run Report",
    ))

    if report.steps:
        step_table = Table(title="Steps")
        step_table.add_column("Step")
        step_table.add_column("Duration", justify="right")
        for step in report.steps:
            step_table.add_row(step.name, f"{step.duration_seconds:.1f}s")
        console.print(step_table)


@cli.command()
@click.option("--project", "-p", help="按项目名称过滤（只在当前 source 内解释）")
@click.option("--session", "-s", "session_id", help="指定单个会话 ID")
@click.option(
    "--mode",
    type=click.Choice(["preprocess", "analyze", "full"], case_sensitive=False),
    default="full",
    show_default=True,
    help="执行到哪个阶段",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["skill", "knowledge"], case_sensitive=False),
    default="skill",
    show_default=True,
    help="输出格式",
)
@click.option("--preview", is_flag=True, help="只预览，不写任何文件或状态")
def run(
    project: str | None,
    session_id: str | None,
    mode: str,
    output_format: str,
    preview: bool,
):
    """运行蒸馏流程，默认使用当前配置中的 source、模型和并发。"""
    if project and session_id:
        raise click.UsageError("`--project` and `--session` cannot be used together.")

    cfg = _load_config()
    cfg.output.format = _resolve_output_format(output_format.lower())

    console.print(Panel(
        f"Source: {cfg.source.type}\n"
        f"Project: {project or '(all in current source)'}\n"
        f"Session: {session_id or '(none)'}\n"
        f"Mode: {mode}\n"
        f"Preview: {preview}\n"
        f"Fast max_concurrency: {cfg.fast_model.max_concurrency}\n"
        f"Strong max_concurrency: {cfg.strong_model.max_concurrency}\n"
        f"Output: {_display_output_format(cfg.output.format)}",
        title="Run Settings",
    ))

    pipeline = DistillPipeline.from_config(cfg)
    pipeline.run(
        project=project,
        session_id=session_id,
        mode=mode.lower(),
        preview=preview,
    )


@cli.group()
def runs():
    """查看历史运行结果。"""


@runs.command("list")
def runs_list():
    """列出已有运行历史。"""
    reports = _iter_reports()
    if not reports:
        console.print("[yellow]No run reports found.[/]")
        return

    table = Table(title="Run History")
    table.add_column("Run ID", width=10)
    table.add_column("Project", width=16)
    table.add_column("Started", width=20)
    table.add_column("Sessions", width=10, justify="right")
    table.add_column("Topics", width=8, justify="right")
    table.add_column("Rules", width=8, justify="right")
    table.add_column("Duration", width=10, justify="right")

    for _, report in reports:
        table.add_row(
            report.run_id,
            (report.project or "")[:16],
            report.started_at[:19],
            f"{report.sessions_passed_filter}/{report.sessions_total}",
            str(report.topics_found),
            str(report.total_rules),
            f"{report.total_duration_seconds:.1f}s",
        )

    console.print(table)


@runs.command("show")
@click.argument("run_id")
def runs_show(run_id: str):
    """查看单次运行的详细信息。"""
    loaded = _load_report(run_id)
    if not loaded:
        console.print(f"[red]Run report not found: {run_id}[/]")
        return

    report_path, report = loaded
    html_path = report_path.with_suffix(".html")

    console.print(Panel(
        f"Run ID: {report.run_id}\n"
        f"Project: {report.project}\n"
        f"Started: {report.started_at}\n"
        f"Finished: {report.finished_at}\n"
        f"Output dir: {report.output_dir or '(none)'}\n"
        f"JSON report: {report_path}\n"
        f"HTML report: {html_path if html_path.exists() else '(missing)'}",
        title="Run Details",
    ))

    summary = Table(title="Summary")
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Sessions total", str(report.sessions_total))
    summary.add_row("Sessions passed", str(report.sessions_passed_filter))
    summary.add_row("Topics", str(report.topics_found))
    summary.add_row("Rules", str(report.total_rules))
    summary.add_row("Duration", f"{report.total_duration_seconds:.1f}s")
    console.print(summary)

    if report.llm_usage:
        llm_table = Table(title="LLM Usage")
        llm_table.add_column("Model")
        llm_table.add_column("Calls", justify="right")
        llm_table.add_column("Input", justify="right")
        llm_table.add_column("Output", justify="right")
        for usage in report.llm_usage:
            llm_table.add_row(
                usage.label,
                str(usage.calls),
                str(usage.input_tokens),
                str(usage.output_tokens),
            )
        console.print(llm_table)


if __name__ == "__main__":
    cli()
