"""Small local web UI for selecting sessions and reviewing extracted memory."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..core.config import (
    DistillConfig,
    LLMConfig,
    init_default_config,
    load_config,
    set_config_value,
)
from ..mining.sources import create_source
from ..orchestrator.pipeline import DistillPipeline
from ..output.formatters.memory_md import (
    AGENT_CONTEXT_FILENAME,
    load_memory_store,
    summarize_memory_quality,
)


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> str:
    """Start the local GUI server and block until interrupted."""
    server = ThreadingHTTPServer((host, port), _make_handler())
    url = f"http://{host}:{server.server_port}"
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url


def session_rows(cfg: DistillConfig, project: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return session rows suitable for the GUI."""
    source = create_source(cfg.source)
    sessions = source.list_sessions(project=project or None)
    sessions.sort(key=lambda s: (s.timestamp, s.msg_count, s.tool_count), reverse=True)
    rows = []
    for session in sessions[:max(1, limit)]:
        tool_count = session.tool_count or source.count_tools(session.id)
        rows.append({
            "id": session.id,
            "title": session.title or "（未命名）",
            "project": session.project or "",
            "messages": session.msg_count,
            "tools": tool_count,
            "date": _format_timestamp(session.timestamp),
        })
    return rows


def memory_snapshot(cfg: DistillConfig, project: str) -> dict[str, Any]:
    """Return current memory quality and top review items for a project."""
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    quality = _localize_quality(summarize_memory_quality(store))
    review_items = _top_review_items(store, limit=8)
    context_path = output_dir / project / AGENT_CONTEXT_FILENAME
    return {
        "project": project,
        "quality": quality,
        "review_items": review_items,
        "agent_context_exists": context_path.exists(),
        "agent_context_path": str(context_path),
    }


# Source-type label order shown in the config UI.
_SOURCE_OPTIONS = ["opencode", "chrys", "codeagent", "claudecode"]
_OUTPUT_OPTIONS = ["memory_md", "knowledge_md", "skill_md"]


def _source_location(cfg: DistillConfig) -> str:
    """Return the active source's path/locator for the current source type."""
    src = cfg.source
    if src.type == "chrys":
        return src.chrys.sessions_dir
    if src.type == "codeagent":
        return src.codeagent.db_path
    if src.type == "claudecode":
        return src.claudecode.projects_dir
    return src.opencode.db_path


def _config_view(cfg: DistillConfig) -> dict[str, Any]:
    """Return a config snapshot for the settings UI (secrets masked)."""
    return {
        "configured": DistillConfig.default_config_path().exists(),
        "config_path": str(DistillConfig.default_config_path()),
        "env_path": str(DistillConfig.env_file_path()),
        "fast": _model_view("fast", cfg.fast_model),
        "strong": _model_view("strong", cfg.strong_model),
        "source": {
            "type": cfg.source.type,
            "options": _SOURCE_OPTIONS,
            "location": _source_location(cfg),
        },
        "output": {
            "format": cfg.output.format,
            "options": _OUTPUT_OPTIONS,
            "skill_output_dir": cfg.output.skill_output_dir,
        },
        "filter": {
            "min_messages": cfg.filter.min_messages,
            "min_tools": cfg.filter.min_tools,
        },
        "analysis": {
            "clustering_max_topics": cfg.analysis.clustering_max_topics,
        },
    }


def _model_view(role: str, model: LLMConfig) -> dict[str, Any]:
    return {
        "role": role,
        "model": model.model,
        "max_tokens": model.max_tokens,
        "max_concurrency": model.max_concurrency,
        "max_rpm": model.max_rpm,
        "base_url": model.base_url,
        "api_key_set": bool(model.api_key),
        "proxy": model.proxy,
        "proxy_bypass": model.proxy_bypass,
        "verify_ssl": model.verify_ssl,
        "timeout": model.timeout,
        "connect_timeout": model.connect_timeout,
        "user_agent": model.user_agent,
    }


def _save_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist settings posted from the config UI.

    Secrets (api_key / base_url) are written to ~/.trace2skill/.env; everything
    else goes to config.yaml via init_default_config + set_config_value.
    """
    # If there is no config yet, create a baseline first so set_config_value
    # has a file to mutate.
    config_path = DistillConfig.default_config_path()
    if not config_path.exists():
        init_default_config(
            api_key=str(payload.get("api_key") or ""),
            base_url=str(payload.get("base_url") or ""),
            fast_model=str(payload.get("fast_model") or "openai/gpt-oss-120b"),
            strong_model=str(payload.get("strong_model") or "openai/gpt-oss-120b"),
            source_type=str(payload.get("source_type") or "opencode"),
            output_format=str(payload.get("output_format") or "memory_md"),
        )

    api_key = payload.get("api_key")
    base_url = payload.get("base_url")
    if api_key or base_url:
        _write_env(api_key, base_url)

    # Non-secret scalar fields -> config.yaml.
    field_map = {
        "fast_model": "fast.model",
        "fast_max_tokens": "fast.max_tokens",
        "fast_max_concurrency": "fast.max_concurrency",
        "fast_max_rpm": "fast.max_rpm",
        "fast_proxy": "fast.proxy",
        "fast_proxy_bypass": "fast.proxy_bypass",
        "fast_verify_ssl": "fast.verify_ssl",
        "fast_timeout": "fast.timeout",
        "fast_connect_timeout": "fast.connect_timeout",
        "fast_user_agent": "fast.user_agent",
        "strong_model": "strong.model",
        "strong_max_tokens": "strong.max_tokens",
        "strong_max_concurrency": "strong.max_concurrency",
        "strong_max_rpm": "strong.max_rpm",
        "source_type": "source.type",
        "source_location": _source_field(payload.get("source_type")),
        "output_format": "output.format",
        "output_skill_output_dir": "output.skill_output_dir",
        "filter_min_messages": "filter.min_messages",
        "filter_min_tools": "filter.min_tools",
        "analysis_clustering_max_topics": "analysis.clustering_max_topics",
    }
    for ui_key, config_key in field_map.items():
        if ui_key not in payload:
            continue
        value = payload[ui_key]
        if value is None or (isinstance(value, str) and value == ""):
            # Skip empties except for proxy fields, where empty = disabled.
            if config_key not in {"fast.proxy", "fast.proxy_bypass"}:
                continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        set_config_value(config_key, str(value))

    # Clear any cached env overrides so the next load reflects the file.
    for stale in ("TRACE2SKILL_API_KEY", "TRACE2SKILL_BASE_URL"):
        os.environ.pop(stale, None)
    return _config_view(load_config())


def _source_field(source_type: Any) -> str:
    """Map a source type to the dotted config key for its locator."""
    return {
        "opencode": "source.opencode.db_path",
        "codeagent": "source.codeagent.db_path",
        "chrys": "source.chrys.sessions_dir",
        "claudecode": "source.claudecode.projects_dir",
    }.get(str(source_type or ""), "source.opencode.db_path")


def _write_env(api_key: Any, base_url: Any) -> None:
    """Write api_key/base_url into ~/.trace2skill/.env, preserving other vars."""
    env_path = DistillConfig.env_file_path()
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    if api_key:
        existing["TRACE2SKILL_API_KEY"] = str(api_key).strip()
    if base_url:
        existing["TRACE2SKILL_BASE_URL"] = str(base_url).strip()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _friendly_error(exc: BaseException) -> str:
    """Turn a pipeline exception into a user-facing Chinese message."""
    text = str(exc)
    low = text.lower()
    if "api_key" in low or "unauthorized" in low or "401" in low:
        return "API Key 缺失或无效，请在「设置」里填写正确的密钥。"
    if "base_url" in low or "connection" in low or "timeout" in low or "connect" in low:
        return "无法连接模型服务，请检查 Base URL、网络或代理设置。"
    if "no sessions" in low or "未找到" in text or "找不到" in text:
        return "所选会话未通过筛选（消息数/工具数不足），或数据源里没有匹配会话。"
    return f"提取失败：{text or exc.__class__.__name__}"


def _make_handler():
    class GuiHandler(BaseHTTPRequestHandler):
        server_version = "Trace2SkillGUI/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_HTML)
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if parsed.path == "/api/config":
                cfg = load_config()
                self._send_json(_config_view(cfg))
                return
            if parsed.path == "/api/sessions":
                query = parse_qs(parsed.query)
                cfg = load_config()
                project = _one(query, "project")
                limit = int(_one(query, "limit") or "100")
                try:
                    self._send_json({"sessions": session_rows(cfg, project=project, limit=limit)})
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"sessions": [], "error": str(exc)})
                return
            if parsed.path == "/api/memory":
                query = parse_qs(parsed.query)
                project = _one(query, "project") or "general"
                self._send_json(memory_snapshot(load_config(), project))
                return
            self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                payload = self._read_json()
                try:
                    self._send_json({"ok": True, "config": _save_config(payload)})
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if parsed.path != "/api/dream":
                self._send_json({"error": "not found"}, status=404)
                return
            payload = self._read_json()
            project = (payload.get("project") or "general").strip() or "general"
            session_ids = [sid for sid in payload.get("session_ids", []) if sid]
            if not session_ids:
                self._send_json({"error": "请至少选择一个会话。"}, status=400)
                return

            try:
                cfg = load_config()
                cfg.output.format = "memory_md"
                pipeline = DistillPipeline.from_config(cfg)
                report = pipeline.run(
                    project=project,
                    session_ids=session_ids,
                    mode="full",
                    preview=False,
                    max_sessions=None,
                    incremental=False,
                )
                snapshot = memory_snapshot(cfg, report.project)
                self._send_json({
                    "run_id": report.run_id,
                    "project": report.project,
                    "sessions": report.sessions_passed_filter,
                    "topics": report.topics_found,
                    "rules": report.total_rules,
                    "memory": snapshot,
                })
            except Exception as exc:  # noqa: BLE001
                # Surface server-side errors as JSON so the UI can show them,
                # instead of dropping the connection ("Failed to fetch").
                self._send_json({"error": _friendly_error(exc)}, status=500)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return GuiHandler


def _top_review_items(store: dict, limit: int) -> list[dict[str, Any]]:
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
    return [
        {
            "id": item.get("id", ""),
            "type": _memory_type_label(item.get("type", "")),
            "reason": _review_reason(item),
            "confidence": float(item.get("confidence", 0) or 0),
            "action": item.get("action", ""),
        }
        for item in candidates[:max(0, limit)]
    ]


def _review_reason(item: dict) -> str:
    if item.get("conflict_with"):
        return "存在冲突"
    if item.get("type") == "OPEN_QUESTION":
        return "开放问题"
    if not item.get("evidence") and not item.get("confirmed"):
        return "缺少证据"
    if float(item.get("confidence", 0) or 0) < 0.55:
        return "置信度较低"
    return "等待复审"


def _localize_quality(quality: dict[str, Any]) -> dict[str, Any]:
    localized = dict(quality)
    localized["label"] = {
        "empty": "暂无记忆",
        "healthy": "健康",
        "usable": "可用",
        "needs review": "需要复审",
        "thin": "记忆偏薄",
    }.get(str(quality.get("label", "")), str(quality.get("label", "")))
    localized["next_actions"] = [_localize_action(action) for action in quality.get("next_actions", [])]
    return localized


def _memory_type_label(memory_type: str) -> str:
    return {
        "USER_PREFERENCE": "用户偏好",
        "STANDING_REQUIREMENT": "长期要求",
        "REPO_FACT": "仓库事实",
        "WORKFLOW_PATTERN": "工作流模式",
        "KNOWLEDGE_DISCOVERY": "知识发现",
        "CORRECTION": "认知纠偏",
        "TOOL_FEEDBACK": "工具反馈",
        "PITFALL": "坑点",
        "OPEN_QUESTION": "开放问题",
    }.get((memory_type or "").upper(), memory_type or "未分类")


def _localize_action(action: str) -> str:
    if action.startswith("Resolve "):
        count = _first_number(action)
        return f"处理 {count} 条互相冲突的记忆。"
    if action.startswith("Add evidence or manually confirm "):
        count = _first_number(action)
        return f"为 {count} 条记忆补充证据，或手动确认。"
    if action.startswith("Answer or archive "):
        count = _first_number(action)
        return f"回答或归档 {count} 个开放问题。"
    if action.startswith("Review "):
        count = _first_number(action)
        return f"复审 {count} 条待确认记忆。"
    if action.startswith("Promote at least one"):
        return "至少确认一条有证据支撑的记忆，让它进入智能体上下文。"
    if action.startswith("No immediate review action"):
        return "当前没有需要立即处理的复审项。"
    return action


def _first_number(text: str) -> str:
    import re
    match = re.search(r"\d+", text)
    return match.group(0) if match else "若干"


def _format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    if ts > 1_000_000_000_000:
        ts = ts // 1000
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def _one(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0].strip()


_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trace2Skill 记忆整理</title>
  <style>
    :root {
      --ink: #18211f;
      --muted: #60706c;
      --line: #d8ded9;
      --paper: #f8faf7;
      --panel: #ffffff;
      --accent: #0d6b5f;
      --accent-2: #9a4d18;
      --warn: #a7392d;
      --shadow: 0 18px 45px rgba(28, 41, 38, .10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-serif, Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(13,107,95,.07) 1px, transparent 1px),
        linear-gradient(rgba(13,107,95,.06) 1px, transparent 1px),
        var(--paper);
      background-size: 28px 28px;
    }
    header {
      padding: 24px 34px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(248,250,247,.92);
      position: sticky;
      top: 0;
      z-index: 2;
      backdrop-filter: blur(10px);
    }
    h1 { margin: 0; font-size: 28px; font-weight: 700; letter-spacing: 0; }
    .sub { margin-top: 6px; color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    main {
      display: grid;
      grid-template-columns: minmax(340px, 1.1fr) minmax(380px, .9fr);
      gap: 18px;
      padding: 18px;
      max-width: 1480px;
      margin: 0 auto;
    }
    section {
      background: rgba(255,255,255,.90);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      min-height: 280px;
    }
    .bar {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 14px;
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }
    h2 { margin: 0; font-size: 20px; }
    label, button, input { font-family: ui-sans-serif, system-ui, sans-serif; }
    .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    input[type=text], input[type=number] {
      height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 0 10px;
      min-width: 150px;
    }
    button {
      height: 36px;
      border: 1px solid var(--ink);
      background: var(--ink);
      color: white;
      padding: 0 12px;
      cursor: pointer;
      font-weight: 650;
    }
    button.secondary { background: white; color: var(--ink); border-color: var(--line); }
    button:disabled { opacity: .45; cursor: wait; }
    .table-wrap { max-height: 64vh; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: #f1f5f0; z-index: 1; }
    tr:hover td { background: #f6f8f4; }
    .id { color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
    .side { padding: 18px; display: grid; gap: 16px; }
    .score {
      display: grid;
      grid-template-columns: 128px 1fr;
      gap: 16px;
      align-items: center;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }
    .dial {
      width: 116px;
      height: 116px;
      border: 12px solid #dce6df;
      border-top-color: var(--accent);
      border-right-color: var(--accent);
      display: grid;
      place-items: center;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 26px;
      font-weight: 800;
    }
    .metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .metric { border: 1px solid var(--line); padding: 10px; background: #fbfcfa; }
    .metric b { display: block; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 20px; }
    .metric span { color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; }
    .list { display: grid; gap: 8px; }
    .item { border-left: 4px solid var(--accent-2); background: #fff8f0; padding: 10px 12px; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    .item small { display: block; color: var(--muted); margin-bottom: 3px; }
    .status { min-height: 22px; color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    .tabs { display: flex; gap: 8px; margin-left: auto; }
    .tab {
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 14px; font-weight: 650;
      padding: 7px 16px; cursor: pointer;
      border: 1px solid var(--line); background: white; color: var(--muted);
    }
    .tab.active { background: var(--ink); color: white; border-color: var(--ink); }
    .panel { display: none; }
    .panel.active { display: block; }
    form.settings { display: grid; gap: 18px; padding: 18px; font-family: ui-sans-serif, system-ui, sans-serif; }
    .field { display: grid; grid-template-columns: 200px 1fr; gap: 12px; align-items: center; }
    .field label { color: var(--muted); font-size: 13px; }
    .field input, .field select {
      height: 34px; border: 1px solid var(--line); background: #fff; padding: 0 10px; font-size: 13px;
    }
    .field input[type=checkbox] { height: auto; width: auto; }
    .field .hint { color: var(--muted); font-size: 12px; }
    .group-title { grid-column: 1 / -1; font-size: 16px; font-weight: 700; padding-top: 10px; border-top: 1px solid var(--line); }
    .settings .bar { grid-column: 1 / -1; }
    @media (max-width: 980px) { main { grid-template-columns: 1fr; } .table-wrap { max-height: none; } .field { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:16px;">
      <div>
        <h1>Trace2Skill 记忆整理</h1>
        <div class="sub">选择有价值的智能体历史会话，提取可长期复用的记忆，并查看哪些内容已经能进入未来上下文。</div>
      </div>
      <div class="tabs">
        <button class="tab active" id="tabExtract">记忆整理</button>
        <button class="tab" id="tabSettings">设置</button>
      </div>
    </div>
  </header>
  <main>
   <div class="panel active" id="extractPanel">
    <section>
      <div class="bar">
        <div>
          <h2>会话选择</h2>
          <div class="sub" id="configSummary">正在读取数据源...</div>
        </div>
        <div class="controls">
          <input id="project" type="text" placeholder="项目筛选" />
          <input id="limit" type="number" min="1" value="80" />
          <button class="secondary" id="load">加载</button>
          <button id="dream">提取所选会话</button>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th></th><th>会话</th><th>项目</th><th>消息</th><th>工具</th><th>日期</th></tr></thead>
          <tbody id="sessions"></tbody>
        </table>
      </div>
    </section>
    <section>
      <div class="bar">
        <div>
          <h2>提取结果</h2>
          <div class="sub">可进入智能体上下文的记忆、待复审内容和下一步建议。</div>
        </div>
        <button class="secondary" id="refresh">刷新</button>
      </div>
      <div class="side">
        <div class="status" id="status">请选择会话，然后开始提取。</div>
        <div class="score">
          <div class="dial" id="score">--</div>
          <div>
            <h2 id="qualityLabel">尚未加载记忆</h2>
            <div class="sub" id="contextPath"></div>
          </div>
        </div>
        <div class="metrics" id="metrics"></div>
        <div>
          <h2>下一步建议</h2>
          <div class="list" id="actions"></div>
        </div>
        <div>
          <h2>待复审内容</h2>
          <div class="list" id="reviewItems"></div>
        </div>
      </div>
    </section>
   </div>

   <div class="panel" id="settingsPanel">
    <section>
      <div class="bar">
        <div>
          <h2>设置</h2>
          <div class="sub" id="settingsHint">在这里配置模型、数据源和输出。API Key 与 Base URL 保存在 .env，其余保存在 config.yaml。</div>
        </div>
        <div class="controls">
          <button id="saveSettings">保存设置</button>
        </div>
      </div>
      <form class="settings" id="settingsForm" autocomplete="off">
        <div class="group-title">模型连接</div>
        <div class="field">
          <label>API Key（写入 .env）</label>
          <input id="cfg_api_key" type="password" placeholder="留空表示不修改现有密钥" />
        </div>
        <div class="field">
          <label>Base URL（写入 .env）</label>
          <input id="cfg_base_url" type="text" placeholder="如 https://api.openai.com/v1" />
        </div>
        <div class="field">
          <label>快速模型（预处理）</label>
          <input id="cfg_fast_model" type="text" />
        </div>
        <div class="field">
          <label>强力模型（蒸馏）</label>
          <input id="cfg_strong_model" type="text" />
        </div>
        <div class="field">
          <label>快速模型并发</label>
          <input id="cfg_fast_max_concurrency" type="number" min="1" />
        </div>
        <div class="field">
          <label>强力模型并发</label>
          <input id="cfg_strong_max_concurrency" type="number" min="1" />
        </div>

        <div class="group-title">数据源</div>
        <div class="field">
          <label>数据源类型</label>
          <select id="cfg_source_type"></select>
        </div>
        <div class="field">
          <label>数据源路径</label>
          <input id="cfg_source_location" type="text" />
        </div>

        <div class="group-title">输出与筛选</div>
        <div class="field">
          <label>输出格式</label>
          <select id="cfg_output_format"></select>
        </div>
        <div class="field">
          <label>技能输出目录</label>
          <input id="cfg_output_skill_output_dir" type="text" />
        </div>
        <div class="field">
          <label>最小消息数</label>
          <input id="cfg_filter_min_messages" type="number" min="0" />
        </div>
        <div class="field">
          <label>最小工具数</label>
          <input id="cfg_filter_min_tools" type="number" min="0" />
        </div>

        <div class="group-title">网络（仅快速模型）</div>
        <div class="field">
          <label>代理地址</label>
          <input id="cfg_fast_proxy" type="text" placeholder="如 socks5://127.0.0.1:1080，留空不使用" />
        </div>
        <div class="field">
          <label>代理绕过</label>
          <input id="cfg_fast_proxy_bypass" type="text" placeholder="不走代理的 host 正则，逗号分隔" />
        </div>
        <div class="field">
          <label>验证 SSL</label>
          <input id="cfg_fast_verify_ssl" type="checkbox" />
        </div>

        <div class="status" id="settingsStatus"></div>
      </form>
    </section>
   </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let currentProject = "general";
    let lastConfig = null;

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }

    function selectedIds() {
      return [...document.querySelectorAll("input[data-session]:checked")].map((x) => x.value);
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function showPanel(name) {
      const isExtract = name === "extract";
      $("tabExtract").classList.toggle("active", isExtract);
      $("tabSettings").classList.toggle("active", !isExtract);
      $("extractPanel").classList.toggle("active", isExtract);
      $("settingsPanel").classList.toggle("active", !isExtract);
      if (!isExtract && lastConfig) fillSettingsForm(lastConfig);
    }

    async function loadConfig() {
      const data = await api("/api/config");
      lastConfig = data;
      const srcLabel = data.source.type;
      $("configSummary").textContent =
        `数据源：${srcLabel} | 输出目录：${data.output.skill_output_dir} | 输出格式：${data.output.format}`;
      $("settingsHint").textContent =
        data.configured
          ? `配置文件：${data.config_path}（API Key / Base URL 存于 .env）`
          : "尚未初始化配置，请在下方填写后保存。";
      fillSettingsForm(data);
    }

    function fillOptions(selectId, options, selected) {
      const sel = $(selectId);
      sel.innerHTML = options.map(o => `<option value="${o}">${o}</option>`).join("");
      if (selected) sel.value = selected;
    }

    function fillSettingsForm(data) {
      fillOptions("cfg_source_type", data.source.options, data.source.type);
      fillOptions("cfg_output_format", data.output.options, data.output.format);
      $("cfg_base_url").value = data.fast.base_url || "";
      $("cfg_fast_model").value = data.fast.model || "";
      $("cfg_strong_model").value = data.strong.model || "";
      $("cfg_fast_max_concurrency").value = data.fast.max_concurrency;
      $("cfg_strong_max_concurrency").value = data.strong.max_concurrency;
      $("cfg_source_location").value = data.source.location || "";
      $("cfg_output_skill_output_dir").value = data.output.skill_output_dir || "";
      $("cfg_filter_min_messages").value = data.filter.min_messages;
      $("cfg_filter_min_tools").value = data.filter.min_tools;
      $("cfg_fast_proxy").value = data.fast.proxy || "";
      $("cfg_fast_proxy_bypass").value = data.fast.proxy_bypass || "";
      $("cfg_fast_verify_ssl").checked = !!data.fast.verify_ssl;
      $("cfg_api_key").value = "";
      $("cfg_api_key").placeholder = data.fast.api_key_set ? "已设置（留空不修改）" : "请输入 API Key";
    }

    function collectSettings() {
      return {
        api_key: $("cfg_api_key").value,
        base_url: $("cfg_base_url").value.trim(),
        fast_model: $("cfg_fast_model").value.trim(),
        strong_model: $("cfg_strong_model").value.trim(),
        fast_max_concurrency: parseInt($("cfg_fast_max_concurrency").value || "1", 10),
        strong_max_concurrency: parseInt($("cfg_strong_max_concurrency").value || "1", 10),
        source_type: $("cfg_source_type").value,
        source_location: $("cfg_source_location").value.trim(),
        output_format: $("cfg_output_format").value,
        output_skill_output_dir: $("cfg_output_skill_output_dir").value.trim(),
        filter_min_messages: parseInt($("cfg_filter_min_messages").value || "0", 10),
        filter_min_tools: parseInt($("cfg_filter_min_tools").value || "0", 10),
        fast_proxy: $("cfg_fast_proxy").value.trim(),
        fast_proxy_bypass: $("cfg_fast_proxy_bypass").value.trim(),
        fast_verify_ssl: $("cfg_fast_verify_ssl").checked,
      };
    }

    async function saveSettings() {
      $("saveSettings").disabled = true;
      $("settingsStatus").textContent = "正在保存...";
      try {
        const data = await api("/api/config", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify(collectSettings())
        });
        lastConfig = data.config;
        fillSettingsForm(data.config);
        $("configSummary").textContent =
          `数据源：${data.config.source.type} | 输出目录：${data.config.output.skill_output_dir} | 输出格式：${data.config.output.format}`;
        $("settingsStatus").textContent = "设置已保存。";
      } catch (err) {
        $("settingsStatus").textContent = err.message;
      } finally {
        $("saveSettings").disabled = false;
      }
    }

    async function loadSessions() {
      const project = $("project").value.trim();
      currentProject = project || "general";
      $("status").textContent = "正在加载会话...";
      try {
        const data = await api(`/api/sessions?project=${encodeURIComponent(project)}&limit=${$("limit").value || 80}`);
        if (data.error) {
          $("sessions").innerHTML = "";
          $("status").textContent = "加载会话失败：" + data.error + "（可在「设置」里检查数据源配置）";
          return;
        }
        $("sessions").innerHTML = data.sessions.map(s => `
          <tr>
            <td><input type="checkbox" data-session value="${s.id}"></td>
            <td><b>${escapeHtml(s.title)}</b><div class="id">${escapeHtml(s.id)}</div></td>
            <td>${escapeHtml(s.project || "")}</td>
            <td>${s.messages}</td>
            <td>${s.tools}</td>
            <td>${escapeHtml(s.date || "")}</td>
          </tr>
        `).join("");
        $("status").textContent = `已加载 ${data.sessions.length} 个会话。请选择值得整理的会话。`;
      } catch (err) {
        $("sessions").innerHTML = "";
        $("status").textContent = err.message;
      }
    }

    async function runDream() {
      const ids = selectedIds();
      if (!ids.length) {
        $("status").textContent = "请至少选择一个会话。";
        return;
      }
      $("dream").disabled = true;
      $("status").textContent = `正在从 ${ids.length} 个所选会话中提取记忆...`;
      try {
        const project = $("project").value.trim() || currentProject || "general";
        const data = await api("/api/dream", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({project, session_ids: ids})
        });
        currentProject = data.project;
        $("status").textContent = `运行 ${data.run_id}：处理 ${data.sessions} 个会话，发现 ${data.topics} 个主题，提取 ${data.rules} 条记忆。`;
        renderMemory(data.memory);
      } catch (err) {
        $("status").textContent = err.message;
      } finally {
        $("dream").disabled = false;
      }
    }

    async function refreshMemory() {
      const project = $("project").value.trim() || currentProject || "general";
      const data = await api(`/api/memory?project=${encodeURIComponent(project)}`);
      renderMemory(data);
    }

    function renderMemory(data) {
      const q = data.quality;
      $("score").textContent = q.score;
      $("qualityLabel").textContent = q.label;
      $("contextPath").textContent = data.agent_context_exists ? data.agent_context_path : "尚未生成智能体上下文文件。";
      $("metrics").innerHTML = [
        ["可进入上下文", q.agent_ready],
        ["需要复审", q.review],
        ["缺少证据", q.missing_evidence],
        ["存在冲突", q.conflict],
        ["开放问题", q.open_questions],
        ["当前总数", q.total],
      ].map(([label, value]) => `<div class="metric"><b>${value}</b><span>${label}</span></div>`).join("");
      $("actions").innerHTML = q.next_actions.map(a => `<div class="item">${escapeHtml(a)}</div>`).join("");
      $("reviewItems").innerHTML = data.review_items.length
        ? data.review_items.map(i => `<div class="item"><small>${escapeHtml(i.reason)} · ${escapeHtml(i.type)} · ${i.confidence.toFixed(2)}</small>${escapeHtml(i.action)}</div>`).join("")
        : `<div class="item">暂无待复审内容。</div>`;
    }

    $("load").addEventListener("click", loadSessions);
    $("dream").addEventListener("click", runDream);
    $("refresh").addEventListener("click", refreshMemory);
    $("saveSettings").addEventListener("click", saveSettings);
    $("tabExtract").addEventListener("click", () => showPanel("extract"));
    $("tabSettings").addEventListener("click", () => showPanel("settings"));
    loadConfig().then(loadSessions).catch(err => $("status").textContent = err.message);
  </script>
</body>
</html>
"""
