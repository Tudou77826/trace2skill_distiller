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

from ..core.config import DistillConfig, LLMConfig, init_default_config, load_config, set_config_value
from ..core.console import console
from ..llm import LLMClient
from ..llm.providers.openai_compatible import OpenAICompatibleProvider
from ..mining.sources import create_source
from ..orchestrator.pipeline import DistillPipeline
from ..output.types import DistillReport
from ..output.formatters.memory_md import (
    AGENT_CONTEXT_FILENAME,
    MEMORY_TYPE_LABELS,
    STORE_FILENAME,
    load_memory_store,
    refresh_memory_files,
    summarize_memory_quality,
)
from ..gui.server import run_gui


INSTALLED_MEMORY_FILENAME = "trace2skill-memory.md"
CLAUDE_IMPORT_MARKER = "<!-- trace2skill-memory-import -->"


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
    return load_config()


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
    elif cfg.source.type == "codeagent":
        return cfg.source.codeagent.db_path
    elif cfg.source.type == "claudecode":
        return cfg.source.claudecode.projects_dir
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
    mapping = {
        "memory": "memory_md",
        "memory_md": "memory_md",
        "knowledge": "knowledge_md",
        "knowledge_md": "knowledge_md",
        "skill": "skill_md",
        "skill_md": "skill_md",
    }
    if output_format not in mapping:
        raise ValueError(f"Unknown output format: {output_format}")
    return mapping[output_format]


def _display_output_format(output_format: str) -> str:
    """Map internal formatter ids to user-facing labels."""
    mapping = {
        "memory_md": "memory",
        "knowledge_md": "knowledge",
        "skill_md": "skill",
    }
    return mapping.get(output_format, output_format)


def _fmt_timestamp(ts: int) -> str:
    """Format source timestamps safely (handles both seconds and milliseconds)."""
    if not ts:
        return ""
    # Auto-detect milliseconds: values > 1e12 are likely ms, not seconds
    if ts > 1_000_000_000_000:
        ts = ts // 1000
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return ""


@click.group()
@click.version_option("0.2.0")
@click.option("--verbose", "-v", is_flag=True, help="显示详细日志（INFO 级别）")
def cli(verbose: bool):
    """Trace2Skill Distiller。

    \b
    常用流程:
      $ trace2skill gui
      $ trace2skill dream --project my-project --install-context
      $ trace2skill memory next --project my-project
      $ trace2skill memory review --project my-project

    \b
    `gui` 是推荐入口：选择历史会话，提取高价值记忆，并查看待复审内容。
    `dream` 是命令行入口：回顾会话，合并长期记忆，并生成 agent context。
    当前数据来源、模型级并发限制等都来自配置文件。
    `project` 和 `session` 只在当前 source 范围内解释，不会跨多种 coding 软件遍历。
    """
    _setup_logging()
    if verbose:
        logging.getLogger("trace2skill_distiller").setLevel(logging.INFO)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Host for the local GUI server.")
@click.option("--port", type=int, default=8765, show_default=True, help="Port for the local GUI server.")
@click.option("--open", "open_browser", is_flag=True, help="Open the GUI in the default browser.")
def gui(host: str, port: int, open_browser: bool):
    """Start the local session selection and memory review GUI."""
    url = f"http://{host}:{port}"
    console.print(f"[green]Trace2Skill GUI:[/] {url}")
    console.print("[dim]Press Ctrl+C to stop.[/]")
    run_gui(host=host, port=port, open_browser=open_browser)


@cli.command()
@click.option("--api-key", prompt="API Key", help="LLM API 密钥")
@click.option("--base-url", prompt="Base URL", help="LLM API 基础地址（如 https://api.openai.com/v1）")
@click.option(
    "--source",
    "-s",
    type=click.Choice(["opencode", "chrys", "codeagent", "claudecode"], case_sensitive=False),
    prompt="数据源类型",
    default="opencode",
    help="选择数据源",
)
@click.option("--fast-model", prompt="快速模型", default="openai/gpt-oss-120b", help="快速模型（用于预处理）")
@click.option("--strong-model", prompt="强力模型", default="openai/gpt-oss-120b", help="强力模型（用于蒸馏）")
@click.option("--fast-concurrency", type=int, prompt="快速模型并发数", default=1, help="快速模型并发请求数")
@click.option("--strong-concurrency", type=int, prompt="强力模型并发数", default=1, help="强力模型并发请求数")
@click.option(
    "--output-format",
    type=click.Choice(["memory_md", "knowledge_md", "skill_md"], case_sensitive=False),
    prompt="输出格式",
    default="memory_md",
    help="技能输出格式",
)
@click.option("--proxy", default="", help="代理地址（如 socks5://127.0.0.1:1080）")
@click.option("--proxy-bypass", default="", help="不走代理的 host 正则，逗号分隔")
@click.option("--verify-ssl/--no-verify-ssl", default=False, help="是否验证 SSL 证书")
@click.option("--timeout", type=float, default=120.0, help="请求超时（秒）")
@click.option("--connect-timeout", type=float, default=10.0, help="连接超时（秒）")
def init(
    api_key: str,
    base_url: str,
    source: str,
    fast_model: str,
    strong_model: str,
    fast_concurrency: int,
    strong_concurrency: int,
    output_format: str,
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
        source_type=source.lower(),
        fast_concurrency=fast_concurrency,
        strong_concurrency=strong_concurrency,
        output_format=output_format.lower(),
    )
    console.print(Panel(
        f"Config created: {path}\n"
        f"API key saved to: {path.parent / '.env'}\n"
        f"Source: {source}\n"
        f"Fast model: {fast_model} (concurrency={fast_concurrency})\n"
        f"Strong model: {strong_model} (concurrency={strong_concurrency})\n"
        f"Output format: {output_format}",
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
        raise SystemExit(1)


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


@cli.group(hidden=True)
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

    console.print(
        f"  [bold]{'#':>3}[/]  "
        f"[bold]{'Session ID':<36}[/]  "
        f"[bold]{'Msgs':>4} {'Tools':>5}[/]  "
        f"[bold]{'Project':<15}[/]  "
        f"[bold]{'Date':<10}[/]  "
        f"[bold]Title[/]"
    )
    console.print(f"  [dim]{'─' * 120}[/]")

    for i, s in enumerate(displayed, start=1):
        console.print(
            f"  [dim]{i:>3}.[/] [cyan]{s.id}[/]  "
            f"[dim]msgs={s.msg_count:>4} tools={s.tool_count:>3}[/]  "
            f"{(s.project or '')[:15]}  "
            f"[dim]{_fmt_timestamp(s.timestamp)}[/]  "
            f"{(s.title or '')[:50]}"
        )

    console.print(
        f"\n[dim]共 {len(displayed)}/{len(filtered)} 条 | "
        f"trace2skill inspect session <ID> | "
        f"trace2skill run -s <ID>[/]"
    )


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


@cli.group(hidden=True)
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
        raise SystemExit(1)

    if not result:
        console.print("[yellow]Session did not pass quality filter.[/]")
        raise SystemExit(1)

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


@cli.command(hidden=True)
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
    type=click.Choice(["memory", "knowledge", "skill"], case_sensitive=False),
    default="memory",
    show_default=True,
    help="输出格式",
)
@click.option("--preview", is_flag=True, help="只预览，不写任何文件或状态")
@click.option("--limit", type=int, default=None, help="最多处理最近 N 个会话")
@click.option("--incremental", is_flag=True, help="跳过已经处理过的会话")
def run(
    project: str | None,
    session_id: str | None,
    mode: str,
    output_format: str,
    preview: bool,
    limit: int | None,
    incremental: bool,
):
    """运行蒸馏流程，默认使用当前配置中的 source、模型和并发。"""
    _run_pipeline_command(
        project=project,
        session_id=session_id,
        mode=mode.lower(),
        output_format=output_format.lower(),
        preview=preview,
        max_sessions=limit,
        incremental=incremental,
        title="Run Settings",
    )


@cli.command()
@click.option("--project", "-p", help="Review sessions from one project.")
@click.option("--session", "-s", "session_id", help="Review one session.")
@click.option("--limit", type=int, default=20, show_default=True, help="Review only the latest N sessions.")
@click.option("--all", "include_all", is_flag=True, help="Include sessions that were already processed.")
@click.option("--preview", is_flag=True, help="Preview without writing memory files or state.")
@click.option("--install-context", is_flag=True, help="Install generated agent context into CLAUDE.md after the review.")
@click.option(
    "--target",
    type=click.Path(path_type=Path),
    default=Path("CLAUDE.md"),
    show_default=True,
    help="Claude Code project memory file to update when --install-context is used.",
)
@click.option(
    "--import-file",
    "import_file",
    type=click.Path(path_type=Path),
    default=Path(INSTALLED_MEMORY_FILENAME),
    show_default=True,
    help="Imported memory file path when --install-context is used.",
)
def dream(
    project: str | None,
    session_id: str | None,
    limit: int,
    include_all: bool,
    preview: bool,
    install_context: bool,
    target: Path,
    import_file: Path,
):
    """Review sessions and consolidate long-term memory."""
    if preview and install_context:
        raise click.UsageError("`--install-context` cannot be used with `--preview`.")

    report = _run_pipeline_command(
        project=project,
        session_id=session_id,
        mode="full",
        output_format="memory",
        preview=preview,
        max_sessions=limit,
        incremental=not include_all,
        title="Dream Review",
    )
    if install_context:
        _install_context_files(report.project, target, import_file)
    if not preview:
        _print_memory_next(report.project, limit=5, missing_ok=True)


def _run_pipeline_command(
    *,
    project: str | None,
    session_id: str | None,
    mode: str,
    output_format: str,
    preview: bool,
    max_sessions: int | None,
    incremental: bool,
    title: str,
) -> DistillReport:
    """Shared execution for advanced run and simple dream commands."""
    if project and session_id:
        raise click.UsageError("`--project` and `--session` cannot be used together.")

    cfg = _load_config()
    cfg.output.format = _resolve_output_format(output_format.lower())

    # Determine effective display values
    if session_id:
        project_display = "(from session)"
    else:
        project_display = project or "(all)"

    console.print(Panel(
        f"Source: {cfg.source.type}\n"
        f"Project: {project_display}\n"
        f"Session: {session_id or '(none)'}\n"
        f"Mode: {mode}\n"
        f"Preview: {preview}\n"
        f"Limit: {max_sessions or '(none)'}\n"
        f"Incremental: {incremental}\n"
        f"Output: {_display_output_format(cfg.output.format)}",
        title=title,
    ))

    pipeline = DistillPipeline.from_config(cfg)
    return pipeline.run(
        project=project,
        session_id=session_id,
        mode=mode,
        preview=preview,
        max_sessions=max_sessions,
        incremental=incremental,
    )


@cli.command(hidden=True)
@click.option("--project", "-p", required=True, help="Project memory to show.")
def context(project: str):
    """Show compact agent context generated by dream."""
    cfg = _load_config()
    path = Path(cfg.output.skill_output_dir).expanduser() / project / AGENT_CONTEXT_FILENAME
    if not path.exists():
        console.print(f"[yellow]No agent context found for project '{project}'. Run `trace2skill dream --project {project}` first.[/]")
        return
    console.print(Panel(path.read_text(encoding="utf-8"), title=str(path)))


@cli.group()
def memory():
    """Manage consolidated memory items."""


@memory.command("show")
@click.argument("memory_id")
@click.option("--project", "-p", required=True, help="Project memory store.")
def memory_show(memory_id: str, project: str):
    """Show one memory item by id or id prefix."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    item = _find_memory_item(store, memory_id)
    if not item:
        console.print(f"[red]Memory not found: {memory_id}[/]")
        raise SystemExit(1)
    console.print(Panel(_format_memory_detail(item), title=f"Memory {item.get('id', '')}"))


@memory.command("stats")
@click.option("--project", "-p", help="Project memory store. Omit to summarize all projects.")
def memory_stats(project: str | None):
    """Show memory store health metrics."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    stores = _load_memory_stores(output_dir, project)
    if not stores:
        console.print("[yellow]No memory stores found. Run `trace2skill dream` first.[/]")
        return

    table = Table(title="Memory Health")
    table.add_column("Project", width=16)
    table.add_column("Score", justify="right")
    table.add_column("Status")
    table.add_column("Total", justify="right")
    table.add_column("Agent", justify="right")
    table.add_column("Review", justify="right")
    table.add_column("Archived", justify="right")
    table.add_column("Conflict", justify="right")
    table.add_column("No Evidence", justify="right")
    table.add_column("Updated")

    totals = {
        "score": 0,
        "total": 0,
        "agent": 0,
        "review": 0,
        "archived": 0,
        "conflict": 0,
        "missing_evidence": 0,
    }
    type_counts: dict[str, int] = {}
    quality_lines: list[str] = []

    for project_name, store in stores:
        stats = _memory_store_stats(store)
        quality = summarize_memory_quality(store)
        quality_lines.append(f"{project_name}: Score {quality['score']}/100, Status {quality['label']}")
        totals["score"] += quality["score"]
        for key in totals:
            if key == "score":
                continue
            totals[key] += stats[key]
        for mem_type, count in stats["types"].items():
            type_counts[mem_type] = type_counts.get(mem_type, 0) + count
        table.add_row(
            project_name[:16],
            str(quality["score"]),
            quality["label"],
            str(stats["total"]),
            str(stats["agent"]),
            str(stats["review"]),
            str(stats["archived"]),
            str(stats["conflict"]),
            str(stats["missing_evidence"]),
            (store.get("updated_at") or "")[:19],
        )

    console.print(table)
    console.print("[dim]Quality: " + "; ".join(quality_lines) + "[/]")

    summary = Table(title="Type Distribution")
    summary.add_column("Type")
    summary.add_column("Count", justify="right")
    for mem_type, count in sorted(type_counts.items()):
        summary.add_row(_display_memory_type(mem_type), str(count))
    console.print(summary)

    if len(stores) > 1:
        avg_score = round(totals["score"] / len(stores))
        console.print(
            f"[dim]Totals: avg score {avg_score}/100, {totals['total']} memories, {totals['agent']} agent-ready, "
            f"{totals['review']} need review, {totals['conflict']} conflicts.[/]"
        )


@memory.command("next")
@click.option("--project", "-p", help="Project memory store. Omit to summarize all projects.")
@click.option("--limit", type=int, default=5, show_default=True, help="Maximum review items to show per project.")
def memory_next(project: str | None, limit: int):
    """Show the next best actions for improving memory quality."""
    _print_memory_next(project, limit=limit, missing_ok=False)


def _print_memory_next(project: str | None, limit: int, missing_ok: bool) -> None:
    """Render memory quality next actions for one or more projects."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    stores = _load_memory_stores(output_dir, project)
    if not stores:
        if not missing_ok:
            console.print("[yellow]No memory stores found. Run `trace2skill dream` first.[/]")
        return

    for project_name, store in stores:
        quality = summarize_memory_quality(store)
        queue = _memory_review_queue(store, limit)
        lines = [
            f"Score: {quality['score']}/100",
            f"Status: {quality['label']}",
            f"Agent-ready: {quality['agent_ready']} / {quality['total']}",
            f"Needs review: {quality['review']}",
            f"Conflicts: {quality['conflict']}",
            f"Missing evidence: {quality['missing_evidence']}",
            "",
            "Next actions:",
        ]
        lines.extend(f"- {action}" for action in quality["next_actions"])
        if queue:
            lines.extend(["", "Top review items:"])
            for item in queue:
                reason = _memory_review_reason(item)
                lines.append(f"- [{item.get('id', '')}] {reason}: {item.get('action', '')}")
            lines.extend([
                "",
                f"Run `trace2skill memory review --project {project_name}` to accept, edit, or archive these items.",
            ])
        else:
            lines.extend(["", "No queued memory items need manual review."])
        console.print(Panel("\n".join(lines), title=f"Memory Next - {project_name}"))


@memory.command("install-context")
@click.option("--project", "-p", required=True, help="Project memory store to install.")
@click.option(
    "--target",
    type=click.Path(path_type=Path),
    default=Path("CLAUDE.md"),
    show_default=True,
    help="Claude Code project memory file to update.",
)
@click.option(
    "--import-file",
    "import_file",
    type=click.Path(path_type=Path),
    default=Path(INSTALLED_MEMORY_FILENAME),
    show_default=True,
    help="Imported memory file path, relative to --target when not absolute.",
)
def memory_install_context(project: str, target: Path, import_file: Path):
    """Install generated agent context into a Claude Code project memory file."""
    _install_context_files(project, target, import_file)


def _install_context_files(project: str, target: Path, import_file: Path) -> None:
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    source_path = output_dir / project / AGENT_CONTEXT_FILENAME
    store_path = output_dir / project / STORE_FILENAME
    if not source_path.exists():
        console.print(
            f"[yellow]No agent context found for project '{project}'. "
            f"Run `trace2skill dream --project {project}` first.[/]"
        )
        raise SystemExit(1)

    target_path = target.expanduser().resolve()
    if import_file.is_absolute():
        import_path = import_file.expanduser().resolve()
        import_ref = import_path.as_posix()
    else:
        import_path = (target_path.parent / import_file).resolve()
        import_ref = import_file.as_posix()

    import_path.parent.mkdir(parents=True, exist_ok=True)
    installed_text = _installed_memory_text(project, source_path, store_path)
    import_path.write_text(installed_text, encoding="utf-8")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_claude_import(target_path, import_ref)

    console.print(Panel(
        f"Claude memory: {target_path}\n"
        f"Imported context: {import_path}\n"
        f"Source context: {source_path}",
        title="Installed Trace2Skill Context",
    ))


@memory.command("archive")
@click.argument("memory_id")
@click.option("--project", "-p", required=True, help="Project memory store.")
def memory_archive(memory_id: str, project: str):
    """Archive a memory so it no longer enters agent context."""
    _update_memory_status(project, memory_id, "archived")


@memory.command("restore")
@click.argument("memory_id")
@click.option("--project", "-p", required=True, help="Project memory store.")
def memory_restore(memory_id: str, project: str):
    """Restore an archived memory to active or review state."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    item = _find_memory_item(store, memory_id)
    if not item:
        console.print(f"[red]Memory not found: {memory_id}[/]")
        raise SystemExit(1)
    status = "review" if item.get("type") == "OPEN_QUESTION" or float(item.get("confidence", 0) or 0) < 0.55 else "active"
    item["status"] = status
    item["updated_by"] = "trace2skill memory restore"
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    refresh_memory_files(output_dir, project, store)
    console.print(f"[green]Restored {item.get('id')} -> {status}[/]")


@memory.command("confirm")
@click.argument("memory_id")
@click.option("--project", "-p", required=True, help="Project memory store.")
@click.option("--confidence", type=float, default=0.8, show_default=True, help="Minimum confidence after confirmation.")
def memory_confirm(memory_id: str, project: str, confidence: float):
    """Confirm a memory and promote it into active agent context."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    item = _find_memory_item(store, memory_id)
    if not item:
        console.print(f"[red]Memory not found: {memory_id}[/]")
        raise SystemExit(1)
    item["confidence"] = max(float(item.get("confidence", 0) or 0), confidence)
    item["seen_count"] = int(item.get("seen_count", 1) or 1) + 1
    item["status"] = "review" if item.get("type") == "OPEN_QUESTION" else "active"
    item["confirmed"] = True
    item["updated_by"] = "trace2skill memory confirm"
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    refresh_memory_files(output_dir, project, store)
    console.print(f"[green]Confirmed {item.get('id')} (confidence={item['confidence']:.2f})[/]")


@memory.command("edit")
@click.argument("memory_id")
@click.option("--project", "-p", required=True, help="Project memory store.")
@click.option("--action", help="Replace the memory text.")
@click.option("--condition", help="Replace the applies-when condition.")
@click.option("--type", "memory_type", help="Replace the memory type.")
@click.option("--scope", help="Replace the scope.")
@click.option("--confidence", type=float, help="Replace the confidence score.")
@click.option("--status", type=click.Choice(["active", "review", "archived"]), help="Replace the review status.")
def memory_edit(
    memory_id: str,
    project: str,
    action: str | None,
    condition: str | None,
    memory_type: str | None,
    scope: str | None,
    confidence: float | None,
    status: str | None,
):
    """Edit a memory item without opening JSON manually."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    item = _find_memory_item(store, memory_id)
    if not item:
        console.print(f"[red]Memory not found: {memory_id}[/]")
        raise SystemExit(1)
    if not any(value is not None for value in [action, condition, memory_type, scope, confidence, status]):
        console.print("[yellow]Nothing to edit. Pass --action, --condition, --type, --scope, --confidence, or --status.[/]")
        return
    _edit_memory_item(
        item,
        action=action,
        condition=condition,
        memory_type=memory_type,
        scope=scope,
        confidence=confidence,
        status=status,
        updated_by="trace2skill memory edit",
    )
    refresh_memory_files(output_dir, project, store)
    console.print(f"[green]Edited {item.get('id')}[/]")


@memory.command("review")
@click.option("--project", "-p", required=True, help="Project memory store.")
@click.option("--limit", type=int, default=20, show_default=True, help="Maximum items to review.")
def memory_review(project: str, limit: int):
    """Interactively review weak memories and open questions."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    queue = _memory_review_queue(store, limit)

    if not queue:
        console.print("[green]No memories need review.[/]")
        return

    changed = False
    console.print(f"[bold]Reviewing {len(queue)} memory item(s) for {project}[/]")
    for index, item in enumerate(queue, start=1):
        console.print()
        console.print(Panel(_format_memory_detail(item), title=f"{index}/{len(queue)}"))
        choice = click.prompt(
            "Action [a=accept, m=edit, e=archive, s=skip, q=quit]",
            default="s",
            show_default=True,
        ).strip().lower()
        if choice in {"q", "quit"}:
            break
        if choice in {"a", "accept", "c", "confirm"}:
            item["confidence"] = max(float(item.get("confidence", 0) or 0), 0.8)
            item["seen_count"] = int(item.get("seen_count", 1) or 1) + 1
            item["status"] = "review" if item.get("type") == "OPEN_QUESTION" else "active"
            item["confirmed"] = True
            item["updated_by"] = "trace2skill memory review accept"
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            changed = True
            console.print(f"[green]Accepted {item.get('id')}[/]")
        elif choice in {"m", "edit"}:
            new_action = click.prompt("New memory text", default=item.get("action", ""), show_default=False)
            new_condition = click.prompt("Applies when (blank keeps current)", default="", show_default=False)
            _edit_memory_item(
                item,
                action=new_action,
                condition=new_condition if new_condition else None,
                confidence=max(float(item.get("confidence", 0) or 0), 0.8),
                status="review" if item.get("type") == "OPEN_QUESTION" else "active",
                confirmed=True,
                updated_by="trace2skill memory review edit",
            )
            changed = True
            console.print(f"[green]Edited {item.get('id')}[/]")
        elif choice in {"e", "archive", "r", "reject"}:
            item["status"] = "archived"
            item["updated_by"] = "trace2skill memory review archive"
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            changed = True
            console.print(f"[yellow]Archived {item.get('id')}[/]")
        elif choice in {"s", "skip", ""}:
            console.print("[dim]Skipped[/]")
        else:
            console.print("[yellow]Unknown action; skipped.[/]")

    if changed:
        refresh_memory_files(output_dir, project, store)
        console.print("[green]Memory artifacts refreshed.[/]")
    else:
        console.print("[dim]No changes made.[/]")


def _update_memory_status(project: str, memory_id: str, status: str) -> None:
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    item = _find_memory_item(store, memory_id)
    if not item:
        console.print(f"[red]Memory not found: {memory_id}[/]")
        raise SystemExit(1)
    item["status"] = status
    item["updated_by"] = f"trace2skill memory {status}"
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    refresh_memory_files(output_dir, project, store)
    console.print(f"[green]Set {item.get('id')} -> {status}[/]")


def _installed_memory_text(project: str, source_path: Path, store_path: Path) -> str:
    generated = datetime.now().isoformat(timespec="seconds")
    body = source_path.read_text(encoding="utf-8").strip()
    lines = [
        f"# Trace2Skill Memory - {project}",
        "",
        "<!-- This file is generated by `trace2skill memory install-context`. -->",
        f"Generated: {generated}",
        f"Source: {source_path}",
    ]
    if store_path.exists():
        lines.append(f"Store: {store_path}")
    lines.extend([
        "",
        "## Agent Context",
        "",
        body or "_No agent-ready memories yet._",
        "",
    ])
    return "\n".join(lines)


def _ensure_claude_import(target_path: Path, import_ref: str) -> None:
    import_line = f"{CLAUDE_IMPORT_MARKER}\n@{import_ref}"
    if target_path.exists():
        content = target_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        if CLAUDE_IMPORT_MARKER in lines:
            marker_index = lines.index(CLAUDE_IMPORT_MARKER)
            if marker_index + 1 < len(lines) and lines[marker_index + 1].startswith("@"):
                lines[marker_index + 1] = f"@{import_ref}"
            else:
                lines.insert(marker_index + 1, f"@{import_ref}")
            target_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return
        if f"@{import_ref}" in lines:
            return
        separator = "\n\n" if content.strip() else ""
        target_path.write_text(
            content.rstrip() + separator + import_line + "\n",
            encoding="utf-8",
        )
        return

    target_path.write_text(
        "# Project Memory\n\n"
        f"{import_line}\n",
        encoding="utf-8",
    )


def _edit_memory_item(
    item: dict,
    *,
    action: str | None = None,
    condition: str | None = None,
    memory_type: str | None = None,
    scope: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
    confirmed: bool | None = True,
    updated_by: str,
) -> None:
    if action is not None:
        item["action"] = action
    if condition is not None:
        item["condition"] = condition
    if memory_type is not None:
        item["type"] = memory_type.strip().upper().replace("-", "_").replace(" ", "_")
    if scope is not None:
        item["scope"] = scope
    if confidence is not None:
        item["confidence"] = confidence
    if status is not None:
        item["status"] = status
    if confirmed is not None:
        item["confirmed"] = confirmed
    item["updated_by"] = updated_by
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _find_memory_item(store: dict, memory_id: str) -> dict | None:
    matches = [
        item for item in store.get("items", [])
        if str(item.get("id", "")).startswith(memory_id)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _format_memory_detail(item: dict) -> str:
    evidence = item.get("evidence", [])
    lines = [
        f"id: {item.get('id', '')}",
        f"type: {item.get('type', '')}",
        f"status: {item.get('status', '')}",
        f"scope: {item.get('scope', '')}",
        f"confidence: {float(item.get('confidence', 0) or 0):.2f}",
        f"seen_count: {item.get('seen_count', 1)}",
        f"first_seen: {item.get('first_seen', '')}",
        f"last_seen: {item.get('last_seen', '')}",
        "",
        item.get("action", ""),
    ]
    if item.get("condition"):
        lines.extend(["", f"Applies when: {item['condition']}"])
    if evidence:
        lines.append("")
        lines.append("Evidence:")
        lines.extend(f"- {entry}" for entry in evidence[:8])
    return "\n".join(lines)


def _memory_review_queue(store: dict, limit: int) -> list[dict]:
    candidates = [
        item for item in store.get("items", [])
        if item.get("status", "active") != "archived"
        and (
            item.get("status") == "review"
            or item.get("type") == "OPEN_QUESTION"
            or float(item.get("confidence", 0) or 0) < 0.55
            or (not item.get("evidence") and not item.get("confirmed"))
        )
    ]
    candidates.sort(key=lambda item: (
        item.get("type") != "OPEN_QUESTION",
        float(item.get("confidence", 0) or 0),
        item.get("action", ""),
    ))
    return candidates[:max(0, limit)]


def _memory_review_reason(item: dict) -> str:
    if item.get("conflict_with"):
        return "conflict"
    if item.get("type") == "OPEN_QUESTION":
        return "open question"
    if not item.get("evidence") and not item.get("confirmed"):
        return "missing evidence"
    if float(item.get("confidence", 0) or 0) < 0.55:
        return "low confidence"
    if item.get("status") == "review":
        return "queued"
    return "review"


@cli.command(hidden=True)
@click.option("--project", "-p", help="Show memories for one project.")
@click.option("--type", "memory_type", help="Filter by memory type, e.g. USER_PREFERENCE.")
@click.option("--weak", is_flag=True, help="Show only low-confidence memories.")
@click.option("--open", "open_only", is_flag=True, help="Show only open questions.")
def review(
    project: str | None,
    memory_type: str | None,
    weak: bool,
    open_only: bool,
):
    """Inspect consolidated memories and review queues."""
    cfg = _load_config()
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    stores = _load_memory_stores(output_dir, project)

    if not stores:
        console.print("[yellow]No memory stores found. Run `trace2skill dream` first.[/]")
        return

    wanted_type = memory_type.upper() if memory_type else None
    if open_only:
        wanted_type = "OPEN_QUESTION"

    rows = []
    for project_name, store in stores:
        for item in store.get("items", []):
            item_type = (item.get("type") or "").upper()
            confidence = float(item.get("confidence", 0) or 0)
            if wanted_type and item_type != wanted_type:
                continue
            if weak and confidence >= 0.55:
                continue
            rows.append((project_name, item, confidence))

    if not rows:
        console.print("[yellow]No memories match the current filters.[/]")
        return

    rows.sort(key=lambda row: (row[0], row[1].get("type", ""), -row[2], row[1].get("action", "")))

    table = Table(title="Memory Review")
    table.add_column("Project", width=14)
    table.add_column("Type", width=20)
    table.add_column("Conf", justify="right", width=6)
    table.add_column("Seen", justify="right", width=5)
    table.add_column("Memory")

    for project_name, item, confidence in rows[:80]:
        action = item.get("action", "")
        if len(action) > 96:
            action = action[:93] + "..."
        table.add_row(
            project_name[:14],
            _display_memory_type(item.get("type", "")),
            f"{confidence:.2f}" if confidence else "",
            str(item.get("seen_count", 1)),
            action,
        )

    console.print(table)
    if len(rows) > 80:
        console.print(f"[dim]Showing 80/{len(rows)} memories. Narrow with --project, --type, --weak, or --open.[/]")


def _load_memory_stores(output_dir: Path, project: str | None) -> list[tuple[str, dict]]:
    """Load one or more project memory stores."""
    if project:
        store = load_memory_store(output_dir, project)
        return [(project, store)] if store.get("items") else []

    stores = []
    if not output_dir.exists():
        return stores
    for store_path in sorted(output_dir.glob("*/memory_store.json")):
        project_name = store_path.parent.name
        store = load_memory_store(output_dir, project_name)
        if store.get("items"):
            stores.append((project_name, store))
    return stores


def _display_memory_type(memory_type: str) -> str:
    memory_type = (memory_type or "").upper()
    return MEMORY_TYPE_LABELS.get(memory_type, memory_type.title())


def _memory_store_stats(store: dict) -> dict:
    items = store.get("items", [])
    stats = {
        "total": len(items),
        "agent": 0,
        "review": 0,
        "archived": 0,
        "conflict": 0,
        "missing_evidence": 0,
        "types": {},
    }
    for item in items:
        mem_type = (item.get("type") or "UNKNOWN").upper()
        stats["types"][mem_type] = stats["types"].get(mem_type, 0) + 1
        status = item.get("status", "active")
        confidence = float(item.get("confidence", 0) or 0)
        has_evidence_or_confirmation = bool(item.get("evidence") or item.get("confirmed"))
        if status == "archived":
            stats["archived"] += 1
        if status == "review":
            stats["review"] += 1
        if item.get("conflict_with"):
            stats["conflict"] += 1
        if status != "archived" and not has_evidence_or_confirmation:
            stats["missing_evidence"] += 1
        if (
            status == "active"
            and item.get("type") != "OPEN_QUESTION"
            and confidence >= 0.55
            and has_evidence_or_confirmation
        ):
            stats["agent"] += 1
    return stats


@cli.group(hidden=True)
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


@cli.command(hidden=True)
@click.option("--source", "-s", type=click.Choice(["opencode", "chrys", "codeagent", "claudecode"]), default="opencode", help="数据源")
@click.option("--days", "-d", type=int, default=30, show_default=True, help="统计最近 N 天")
@click.option("--project", "-p", help="按项目名称过滤（子串匹配）")
def usage(source: str, days: int, project: str | None):
    """查看最近 N 天的 token 消耗统计。"""
    import json
    import sqlite3
    from collections import defaultdict

    cfg = _load_config()
    source_type = source

    # Calculate cutoff timestamp (milliseconds for OpenCode)
    cutoff_ms = int(datetime.now().timestamp() * 1000) - days * 24 * 60 * 60 * 1000

    stats_by_model: dict[str, dict] = defaultdict(lambda: {
        "input": 0, "output": 0, "calls": 0,
    })

    if source_type == "opencode":
        db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        if not db_path.exists():
            console.print(f"[red]OpenCode database not found: {db_path}[/]")
            raise SystemExit(1)

        conn = sqlite3.connect(str(db_path))
        try:
            # Query assistant messages with token data
            query = """
                SELECT m.data, s.directory
                FROM message m
                JOIN session s ON m.session_id = s.id
                WHERE m.time_created > ?
                  AND m.data LIKE '%"tokens":%'
                  AND m.data LIKE '%"role":"assistant"%'
            """
            params = [cutoff_ms]
            if project:
                safe_project = project.replace("%", "\\%").replace("_", "\\_")
                query += " AND s.directory LIKE ? ESCAPE '\\'"
                params.append(f"%{safe_project}%")

            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        for row in rows:
            data_json, directory = row
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                continue

            tokens = data.get("tokens", {})
            model_id = data.get("modelID", "unknown")

            input_tokens = tokens.get("input", 0)
            output_tokens = tokens.get("output", 0)

            if input_tokens or output_tokens:
                stats_by_model[model_id]["input"] += input_tokens
                stats_by_model[model_id]["output"] += output_tokens
                stats_by_model[model_id]["calls"] += 1

    elif source_type == "chrys":
        chrys_dir = Path(os.environ.get("APPDATA", "")) / "chrys" / "sessions"
        if not chrys_dir.exists():
            console.print(f"[red]Chrys sessions directory not found: {chrys_dir}[/]")
            raise SystemExit(1)

        cutoff_str = datetime.fromtimestamp(cutoff_ms / 1000).isoformat()

        for session_dir in chrys_dir.iterdir():
            if not session_dir.is_dir():
                continue

            session_file = session_dir / "session.json"
            if not session_file.exists():
                continue

            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                continue

            # Check timestamp
            meta = data.get("meta", {})
            updated_at = meta.get("updated_at", "")
            if updated_at and updated_at < cutoff_str:
                continue

            # Filter by project
            cwd = meta.get("primary_cwd", "")
            if project and project.lower() not in cwd.lower():
                continue

            # Extract usage
            state = data.get("state", {})
            last_usage = state.get("last_usage", {})
            model_id = meta.get("model_id", "unknown")

            input_tokens = last_usage.get("input_token_count", 0)
            output_tokens = last_usage.get("output_token_count", 0)

            if input_tokens or output_tokens:
                stats_by_model[model_id]["input"] += input_tokens
                stats_by_model[model_id]["output"] += output_tokens
                stats_by_model[model_id]["calls"] += 1

    elif source_type == "codeagent":
        db_path = Path.home() / ".local" / "share" / "opencode" / "db" / "ngagent.db"
        if not db_path.exists():
            console.print(f"[red]CodeAgent database not found: {db_path}[/]")
            raise SystemExit(1)

        conn = sqlite3.connect(str(db_path))
        try:
            query = """
                SELECT m.data, s.directory
                FROM message m
                JOIN session s ON m.session_id = s.id
                WHERE m.time_created > ?
                  AND m.data LIKE '%"tokens":%'
                  AND m.data LIKE '%"role":"assistant"%'
            """
            params = [cutoff_ms]
            if project:
                safe_project = project.replace("%", "\\%").replace("_", "\\_")
                query += " AND s.directory LIKE ? ESCAPE '\\'"
                params.append(f"%{safe_project}%")

            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        for row in rows:
            data_json, directory = row
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                continue

            tokens = data.get("tokens", {})
            model_id = data.get("modelID", "unknown")

            input_tokens = tokens.get("input", 0)
            output_tokens = tokens.get("output", 0)

            if input_tokens or output_tokens:
                stats_by_model[model_id]["input"] += input_tokens
                stats_by_model[model_id]["output"] += output_tokens
                stats_by_model[model_id]["calls"] += 1

    elif source_type == "claudecode":
        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            console.print(f"[red]Claude Code projects directory not found: {projects_dir}[/]")
            raise SystemExit(1)

        cutoff_dt = datetime.fromtimestamp(cutoff_ms / 1000)

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue

            for jsonl_file in project_dir.glob("*.jsonl"):
                try:
                    with open(jsonl_file, encoding="utf-8") as f:
                        for line in f:
                            try:
                                d = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            if d.get("type") != "assistant":
                                continue

                            # Check timestamp
                            ts = d.get("timestamp", "")
                            if ts:
                                try:
                                    if ts.endswith("Z"):
                                        ts = ts[:-1] + "+00:00"
                                    msg_dt = datetime.fromisoformat(ts)
                                    if msg_dt < cutoff_dt:
                                        continue
                                except Exception:
                                    pass

                            msg = d.get("message", {})
                            usage = msg.get("usage", {})
                            model_id = msg.get("model", "unknown")

                            input_tokens = usage.get("input_tokens", 0)
                            output_tokens = usage.get("output_tokens", 0)

                            if input_tokens or output_tokens:
                                stats_by_model[model_id]["input"] += input_tokens
                                stats_by_model[model_id]["output"] += output_tokens
                                stats_by_model[model_id]["calls"] += 1
                except IOError:
                    continue

    if not stats_by_model:
        console.print(f"[yellow]No token usage data found for the last {days} days.[/]")
        return

    # Format numbers
    def fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        elif n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    # Calculate totals
    total_input = sum(s["input"] for s in stats_by_model.values())
    total_output = sum(s["output"] for s in stats_by_model.values())
    total_calls = sum(s["calls"] for s in stats_by_model.values())

    # Display summary
    console.print(Panel(
        f"Input:  {fmt_tokens(total_input)}\n"
        f"Output: {fmt_tokens(total_output)}\n"
        f"Total:  {fmt_tokens(total_input + total_output)}\n"
        f"Calls:  {total_calls}",
        title=f"Token Usage (last {days} days)",
    ))

    # Display by-model table
    model_table = Table(title="By Model")
    model_table.add_column("Model", width=24)
    model_table.add_column("Input", justify="right", width=10)
    model_table.add_column("Output", justify="right", width=10)
    model_table.add_column("Total", justify="right", width=10)
    model_table.add_column("Calls", justify="right", width=8)

    for model_id, stats in sorted(
        stats_by_model.items(),
        key=lambda x: x[1]["input"] + x[1]["output"],
        reverse=True
    ):
        total = stats["input"] + stats["output"]
        model_table.add_row(
            model_id[:24],
            fmt_tokens(stats["input"]),
            fmt_tokens(stats["output"]),
            fmt_tokens(total),
            str(stats["calls"]),
        )

    console.print(model_table)


if __name__ == "__main__":
    cli()
