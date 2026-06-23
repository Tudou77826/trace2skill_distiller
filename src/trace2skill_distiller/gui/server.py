"""Small local web UI for selecting sessions and reviewing extracted memory."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..core.config import DistillConfig
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
                cfg = DistillConfig.load()
                self._send_json({
                    "source": cfg.source.type,
                    "output_dir": str(Path(cfg.output.skill_output_dir).expanduser()),
                    "format": cfg.output.format,
                })
                return
            if parsed.path == "/api/sessions":
                query = parse_qs(parsed.query)
                cfg = DistillConfig.load()
                project = _one(query, "project")
                limit = int(_one(query, "limit") or "100")
                self._send_json({"sessions": session_rows(cfg, project=project, limit=limit)})
                return
            if parsed.path == "/api/memory":
                query = parse_qs(parsed.query)
                project = _one(query, "project") or "general"
                self._send_json(memory_snapshot(DistillConfig.load(), project))
                return
            self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/dream":
                self._send_json({"error": "not found"}, status=404)
                return
            payload = self._read_json()
            project = (payload.get("project") or "general").strip() or "general"
            session_ids = [sid for sid in payload.get("session_ids", []) if sid]
            if not session_ids:
                self._send_json({"error": "请至少选择一个会话。"}, status=400)
                return

            cfg = DistillConfig.load()
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
    @media (max-width: 980px) { main { grid-template-columns: 1fr; } .table-wrap { max-height: none; } }
  </style>
</head>
<body>
  <header>
    <h1>Trace2Skill 记忆整理</h1>
    <div class="sub">选择有价值的智能体历史会话，提取可长期复用的记忆，并查看哪些内容已经能进入未来上下文。</div>
  </header>
  <main>
    <section>
      <div class="bar">
        <div>
          <h2>会话选择</h2>
          <div class="sub" id="config">正在读取数据源...</div>
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
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let currentProject = "general";

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }

    function selectedIds() {
      return [...document.querySelectorAll("input[data-session]:checked")].map((x) => x.value);
    }

    async function loadConfig() {
      const data = await api("/api/config");
      $("config").textContent = `数据源：${data.source} | 输出目录：${data.output_dir}`;
    }

    async function loadSessions() {
      const project = $("project").value.trim();
      currentProject = project || "general";
      $("status").textContent = "正在加载会话...";
      const data = await api(`/api/sessions?project=${encodeURIComponent(project)}&limit=${$("limit").value || 80}`);
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

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    $("load").addEventListener("click", loadSessions);
    $("dream").addEventListener("click", runDream);
    $("refresh").addEventListener("click", refreshMemory);
    loadConfig().then(loadSessions).catch(err => $("status").textContent = err.message);
  </script>
</body>
</html>
"""
