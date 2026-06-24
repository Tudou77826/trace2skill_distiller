"""Legacy browser-based GUI (deprecated).

The native desktop app lives in ``qt_app.py`` and is the supported entry point.
This module still hosts the small HTTP server + embedded HTML for backward
compatibility with ``trace2skill gui``, but all backend logic now lives in
``services.py``.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..core.config import load_config
from ..orchestrator.pipeline import DistillPipeline


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> str:
    """Start the local GUI server and block until interrupted."""
    server = ThreadingHTTPServer((host, port), _make_handler())
    # Let request handlers trigger a clean shutdown from the /api/quit endpoint.
    _shutdown_holder["fn"] = server.shutdown
    url = f"http://{host}:{server.server_port}"
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url


_shutdown_holder: dict[str, Any] = {"fn": lambda: None}


# Backend logic now lives in services.py; re-export under the legacy private
# names so the HTTP handlers below keep working unchanged.
from .services import (  # noqa: E402
    _format_timestamp,
    _source_location,
    _SOURCE_OPTIONS,
    config_view as _config_view,
    friendly_error as _friendly_error,
    memory_groups as _memory_groups,
    memory_snapshot,
    run_status_payload as _run_status_payload,
    save_config as _save_config,
    session_rows,
    update_memory_item as _update_memory_item,
)


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
            if parsed.path == "/api/quit":
                # Acknowledge, then shut the server down from another thread so
                # this response is flushed first. This lets users cleanly exit
                # the no-console exe instead of leaving an orphan process.
                self._send_json({"ok": True})
                threading.Timer(0.25, _shutdown_holder["fn"]).start()
                return
            if parsed.path == "/api/config":
                payload = self._read_json()
                try:
                    self._send_json({"ok": True, "config": _save_config(payload)})
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if parsed.path == "/api/memory/item":
                payload = self._read_json()
                try:
                    self._send_json({"ok": True, "memory": _update_memory_item(load_config(), payload)})
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
                    **_run_status_payload(report),
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
      padding: 18px;
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
    tr.ineligible td { color: #7b6f6a; background: #faf8f5; }
    .id { color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
    .hint { color: var(--warn); font-size: 11px; margin-top: 4px; }
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
    .result-head {
      display: grid;
      gap: 8px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .result-head h2 { font-size: 19px; }
    .summary { color: var(--ink); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 14px; line-height: 1.55; }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      border: 1px solid var(--line);
      background: #fbfcfa;
      color: var(--muted);
      padding: 5px 9px;
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 12px;
    }
    .memory-card {
      border: 1px solid var(--line);
      background: white;
      padding: 12px;
      display: grid;
      gap: 7px;
      font-family: ui-sans-serif, system-ui, sans-serif;
    }
    .memory-card.review { background: #fff8f0; border-color: #e6cdb6; }
    .lanes { display: grid; gap: 10px; }
    .lane {
      border: 1px solid var(--line);
      background: #fbfcfa;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .lane-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
    .lane-title { font-family: ui-sans-serif, system-ui, sans-serif; font-weight: 800; font-size: 14px; }
    .lane-subtitle { color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; line-height: 1.45; }
    .lane-count { color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
    .memory-top { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .badge { background: #eef5f2; color: var(--accent); padding: 3px 7px; font-size: 11px; font-weight: 700; }
    .badge.review { background: #fff0df; color: var(--accent-2); }
    .memory-action { font-size: 14px; line-height: 1.55; color: var(--ink); }
    .memory-meta { color: var(--muted); font-size: 12px; line-height: 1.45; }
    .evidence { color: #475652; font-size: 12px; line-height: 1.45; }
    .card-actions { display: grid; gap: 6px; padding-top: 4px; }
    .card-actions button {
      min-height: 48px;
      height: auto;
      padding: 7px 9px;
      font-size: 12px;
      border-color: var(--line);
      background: #fff;
      color: var(--ink);
      text-align: left;
      display: grid;
      gap: 2px;
    }
    .card-actions button.primary-action { background: var(--accent); color: #fff; border-color: var(--accent); }
    .card-actions button.warn-action { color: var(--warn); }
    .action-title { font-weight: 800; line-height: 1.2; }
    .action-effect { color: var(--muted); font-weight: 500; line-height: 1.35; }
    .primary-action .action-effect { color: rgba(255,255,255,.82); }
    .warn-action .action-effect { color: #7f534d; }
    .artifact { color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 11px; overflow-wrap: anywhere; }
    .status { min-height: 22px; color: var(--muted); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    .status.busy { color: var(--accent); font-weight: 600; }
    .spinner {
      display: inline-block; width: 13px; height: 13px;
      margin-right: 7px; vertical-align: -2px;
      border: 2px solid var(--line);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin .8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    button:disabled .spinner { border-top-color: #888; }
    .tabs { display: flex; gap: 8px; margin-left: auto; }
    .tab {
      font-family: ui-sans-serif, system-ui, sans-serif;
      font-size: 14px; font-weight: 650;
      padding: 7px 16px; cursor: pointer;
      border: 1px solid var(--line); background: white; color: var(--muted);
    }
    .tab.active { background: var(--ink); color: white; border-color: var(--ink); }
    #quitBtn { color: var(--warn); border-color: var(--line); }
    #quitBtn:hover { background: var(--warn); color: white; border-color: var(--warn); }
    .panel { display: none; }
    .panel.active {
      display: grid;
      grid-template-columns: minmax(340px, 1.1fr) minmax(380px, .9fr);
      gap: 18px;
      max-width: 1480px;
      margin: 0 auto;
    }
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
    #settingsPanel.active { grid-template-columns: 1fr; max-width: 920px; }
    @media (max-width: 980px) { .panel.active { grid-template-columns: 1fr; } .table-wrap { max-height: none; } .field { grid-template-columns: 1fr; } }
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
        <button class="tab" id="quitBtn" title="关闭后台服务并退出程序">✕ 退出</button>
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
          <input id="project" type="text" placeholder="项目筛选" aria-label="项目筛选" />
          <input id="limit" type="number" min="1" value="80" aria-label="加载会话数量" />
          <button class="secondary" id="load">加载</button>
          <button id="dream"><span class="spinner" style="display:none" id="dreamSpin"></span>提取所选会话</button>
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
          <div class="sub">检查这次真正沉淀下来的工作记忆，决定哪些应该保留。</div>
        </div>
        <button class="secondary" id="refresh">刷新</button>
      </div>
      <div class="side">
        <div class="status" id="status">请选择会话，然后开始提取。</div>
        <div class="result-head">
          <h2 id="qualityLabel">尚未加载结果</h2>
          <div class="summary" id="resultSummary">选择会话后，这里会显示从会话里学到的可复用内容。</div>
          <div class="chips" id="metrics"></div>
        </div>
        <div>
          <h2>审阅记忆</h2>
          <div class="lanes" id="memoryLanes"></div>
        </div>
        <div>
          <h2>建议操作</h2>
          <div class="list" id="actions"></div>
        </div>
        <div>
          <h2>保存位置</h2>
          <div class="artifact" id="contextPath"></div>
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
          <label for="cfg_api_key">API Key（写入 .env）</label>
          <input id="cfg_api_key" type="password" placeholder="留空表示不修改现有密钥" aria-label="API Key" />
        </div>
        <div class="field">
          <label for="cfg_base_url">Base URL（写入 .env）</label>
          <input id="cfg_base_url" type="text" placeholder="如 https://api.openai.com/v1" aria-label="Base URL" />
        </div>
        <div class="field">
          <label for="cfg_fast_model">快速模型（预处理）</label>
          <input id="cfg_fast_model" type="text" aria-label="快速模型" />
        </div>
        <div class="field">
          <label for="cfg_strong_model">强力模型（蒸馏）</label>
          <input id="cfg_strong_model" type="text" aria-label="强力模型" />
        </div>
        <div class="field">
          <label for="cfg_fast_max_concurrency">快速模型并发</label>
          <input id="cfg_fast_max_concurrency" type="number" min="1" aria-label="快速模型并发" />
        </div>
        <div class="field">
          <label for="cfg_strong_max_concurrency">强力模型并发</label>
          <input id="cfg_strong_max_concurrency" type="number" min="1" aria-label="强力模型并发" />
        </div>

        <div class="group-title">数据源</div>
        <div class="field">
          <label for="cfg_source_type">数据源类型</label>
          <select id="cfg_source_type" aria-label="数据源类型"></select>
        </div>
        <div class="field">
          <label for="cfg_source_location">数据源路径</label>
          <input id="cfg_source_location" type="text" aria-label="数据源路径" />
        </div>

        <div class="group-title">输出与筛选</div>
        <div class="field">
          <label for="cfg_output_skill_output_dir">技能输出目录</label>
          <input id="cfg_output_skill_output_dir" type="text" aria-label="技能输出目录" />
        </div>
        <div class="field">
          <label for="cfg_output_agent_context_path">Agent 上下文文件</label>
          <input id="cfg_output_agent_context_path" type="text" placeholder="留空=默认 agent-context.md；填路径则追加到该文件" aria-label="Agent 上下文文件" />
        </div>
        <div class="field">
          <label for="cfg_output_user_profile_path">用户偏好文件</label>
          <input id="cfg_output_user_profile_path" type="text" placeholder="留空=默认 user-profile.md；填路径则追加到该文件" aria-label="用户偏好文件" />
        </div>
        <div class="field">
          <label for="cfg_output_repo_facts_path">仓库事实文件</label>
          <input id="cfg_output_repo_facts_path" type="text" placeholder="留空=默认 repo-facts.md；填路径则追加到该文件" aria-label="仓库事实文件" />
        </div>
        <div class="field">
          <label for="cfg_filter_min_messages">最小消息数</label>
          <input id="cfg_filter_min_messages" type="number" min="0" aria-label="最小消息数" />
        </div>
        <div class="field">
          <label for="cfg_filter_min_tools">最小工具数</label>
          <input id="cfg_filter_min_tools" type="number" min="0" aria-label="最小工具数" />
        </div>

        <div class="group-title">网络（仅快速模型）</div>
        <div class="field">
          <label for="cfg_fast_proxy">代理地址</label>
          <input id="cfg_fast_proxy" type="text" placeholder="如 socks5://127.0.0.1:1080，留空不使用" aria-label="代理地址" />
        </div>
        <div class="field">
          <label for="cfg_fast_proxy_bypass">代理绕过</label>
          <input id="cfg_fast_proxy_bypass" type="text" placeholder="不走代理的 host 正则，逗号分隔" aria-label="代理绕过" />
        </div>
        <div class="field">
          <label for="cfg_fast_verify_ssl">验证 SSL</label>
          <input id="cfg_fast_verify_ssl" type="checkbox" aria-label="验证 SSL" />
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
    let memoryTargetName = "agent-context.md";

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
        `数据源：${srcLabel} | 输出目录：${data.output.skill_output_dir}`;
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
      $("cfg_base_url").value = data.fast.base_url || "";
      $("cfg_fast_model").value = data.fast.model || "";
      $("cfg_strong_model").value = data.strong.model || "";
      $("cfg_fast_max_concurrency").value = data.fast.max_concurrency;
      $("cfg_strong_max_concurrency").value = data.strong.max_concurrency;
      $("cfg_source_location").value = data.source.location || "";
      $("cfg_output_skill_output_dir").value = data.output.skill_output_dir || "";
      $("cfg_output_agent_context_path").value = data.output.agent_context_path || "";
      $("cfg_output_user_profile_path").value = data.output.user_profile_path || "";
      $("cfg_output_repo_facts_path").value = data.output.repo_facts_path || "";
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
        output_skill_output_dir: $("cfg_output_skill_output_dir").value.trim(),
        output_agent_context_path: $("cfg_output_agent_context_path").value.trim(),
        output_user_profile_path: $("cfg_output_user_profile_path").value.trim(),
        output_repo_facts_path: $("cfg_output_repo_facts_path").value.trim(),
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
          `数据源：${data.config.source.type} | 输出目录：${data.config.output.skill_output_dir}`;
        $("settingsStatus").textContent = "设置已保存。";
      } catch (err) {
        $("settingsStatus").textContent = err.message;
      } finally {
        $("saveSettings").disabled = false;
      }
    }

    async function quitApp() {
      $("quitBtn").disabled = true;
      try {
        await fetch("/api/quit", {method: "POST"});
      } catch (_) { /* server is shutting down — expected */ }
      document.body.innerHTML =
        '<div style="font-family:ui-sans-serif,system-ui,sans-serif;padding:60px;text-align:center;color:#60706c;">' +
        '<h2 style="color:#18211f;">程序已关闭，可以关闭这个标签页。</h2>' +
        '<p>需要再次使用时，双击 trace2skill-gui.exe 即可。</p></div>';
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
          <tr class="${s.eligible ? "" : "ineligible"}">
            <td><input type="checkbox" data-session value="${s.id}" aria-label="选择会话 ${escapeHtml(s.title)}"></td>
            <td>
              <b>${escapeHtml(s.title)}</b>
              <div class="id">${escapeHtml(s.id)}</div>
              ${s.eligible ? "" : `<div class="hint">不会产生结果：${escapeHtml(s.hint || "不满足筛选")}</div>`}
            </td>
            <td>${escapeHtml(s.project || "")}</td>
            <td>${s.messages}</td>
            <td>${s.tools}</td>
            <td>${escapeHtml(s.date || "")}</td>
          </tr>
        `).join("");
        const eligibleCount = data.sessions.filter(s => s.eligible).length;
        $("status").textContent =
          `已加载 ${data.sessions.length} 个会话，其中 ${eligibleCount} 个满足当前筛选。优先选择没有红色提示的会话。`;
      } catch (err) {
        $("sessions").innerHTML = "";
        $("status").textContent = err.message;
      }
    }

    let dreamTimer = null;
    function setBusy(busy) {
      $("dream").disabled = busy;
      $("dreamSpin").style.display = busy ? "inline-block" : "none";
      $("status").classList.toggle("busy", busy);
      if (busy) {
        if (dreamTimer) clearInterval(dreamTimer);
        const startedAt = Date.now();
        const stages = ["读取会话", "整理轨迹", "聚类主题", "提取记忆", "写入结果"];
        let stage = 0;
        const tick = () => {
          const secs = Math.floor((Date.now() - startedAt) / 1000);
          const label = stages[Math.min(stage, stages.length - 1)];
          $("status").innerHTML =
            `<span class="spinner"></span>正在提取 ${selectedIds().length} 个会话：${label}（${secs}s）`;
          if (secs > 0 && secs % 12 === 0 && stage < stages.length - 1) stage++;
        };
        tick();
        dreamTimer = setInterval(tick, 1000);
      } else {
        if (dreamTimer) clearInterval(dreamTimer);
        dreamTimer = null;
        $("status").classList.remove("busy");
      }
    }

    async function runDream() {
      const ids = selectedIds();
      if (!ids.length) {
        $("status").textContent = "请至少选择一个会话。";
        return;
      }
      setBusy(true);
      try {
        const project = $("project").value.trim() || currentProject || "general";
        const data = await api("/api/dream", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({project, session_ids: ids})
        });
        currentProject = data.project;
        setBusy(false);
        $("status").textContent = data.message || `运行 ${data.run_id} 已结束。`;
        renderMemory(data.memory, data);
      } catch (err) {
        setBusy(false);
        $("status").textContent = err.message;
      }
    }

    async function refreshMemory() {
      const project = $("project").value.trim() || currentProject || "general";
      $("status").textContent = "正在刷新记忆...";
      try {
        const data = await api(`/api/memory?project=${encodeURIComponent(project)}`);
        renderMemory(data, null);
        $("status").textContent = data.quality.total
          ? `已刷新 ${data.quality.total} 条记忆。`
          : "这个项目还没有可展示的记忆。";
      } catch (err) {
        $("status").textContent = err.message;
      }
    }

    function renderMemory(data, run = null) {
      const q = data.quality;
      $("qualityLabel").textContent = q.total
        ? `学到了 ${q.total} 条可复用记忆`
        : "还没有可展示的结果";
      $("resultSummary").textContent = run?.message || data.summary || "";
      $("contextPath").textContent = data.agent_context_exists
        ? data.agent_context_path
        : "尚未生成可用于后续会话的上下文文件。";
      memoryTargetName = data.agent_context_path
        ? data.agent_context_path.split(/[\\/]/).pop()
        : "agent-context.md";
      $("metrics").innerHTML = [
        ["可直接使用", q.agent_ready],
        ["需要确认", q.review],
        ["开放问题", q.open_questions],
        ["可信度", `${q.score}/100`],
      ].map(([label, value]) => `<span class="chip">${label}：${value}</span>`).join("");
      $("memoryLanes").innerHTML = data.groups?.length
        ? data.groups.map(renderMemoryLane).join("")
        : `<div class="item">这次还没有提取出值得保留的内容。</div>`;
      const actions = [];
      if (run && run.status !== "ok" && run.action) actions.push(run.action);
      if (!run || run.status === "ok") actions.push(...(q.next_actions || []));
      $("actions").innerHTML = [...new Set(actions)].map(a => `<div class="item">${escapeHtml(a)}</div>`).join("");
    }

    function renderMemoryLane(group) {
      const cards = group.items?.length
        ? group.items.map(renderMemoryCard).join("")
        : `<div class="item">暂无内容。</div>`;
      return `
        <div class="lane" data-lane="${escapeHtml(group.id)}">
          <div class="lane-head">
            <div>
              <div class="lane-title">${escapeHtml(group.title)}</div>
              <div class="lane-subtitle">${escapeHtml(group.subtitle)}</div>
            </div>
            <div class="lane-count">${group.items?.length || 0}</div>
          </div>
          ${cards}
        </div>
      `;
    }

    function renderMemoryCard(item) {
      const evidence = item.evidence?.length
        ? `<div class="evidence">依据：${item.evidence.map(escapeHtml).join("；")}</div>`
        : "";
      const condition = item.condition
        ? `<div class="memory-meta">适用场景：${escapeHtml(item.condition)}</div>`
        : "";
      const canConfirm = item.tone === "review" || item.ai_use !== "已确认，可进入长期上下文";
      return `
        <div class="memory-card ${item.tone === "review" ? "review" : ""}" data-memory-id="${escapeHtml(item.id)}">
          <div class="memory-top">
            <span class="badge ${item.tone === "review" ? "review" : ""}">${escapeHtml(item.status)}</span>
            <span class="badge">${escapeHtml(item.type)}</span>
            <span class="memory-meta">${escapeHtml(item.scope)} · 可信度 ${Math.round((item.confidence || 0) * 100)}%</span>
          </div>
          <div class="memory-action">${escapeHtml(item.action)}</div>
          <div class="memory-meta">用途：${escapeHtml(item.why)}</div>
          <div class="memory-meta">AI 使用：${escapeHtml(item.ai_use)}</div>
          ${condition}
          ${evidence}
          <div class="card-actions">
            ${canConfirm ? renderMemoryDecision(item.id, "confirm") : ""}
            ${renderMemoryDecision(item.id, "review")}
            ${renderMemoryDecision(item.id, "archive")}
          </div>
        </div>
      `;
    }

    function memoryDecisionCopy(action) {
      const target = memoryTargetName || "agent-context.md";
      return {
        confirm: {
          title: "保存到当前项目记忆文件",
          effect: `写入 ${target}，后续 AI 会按这条记忆工作。`,
          className: "primary-action",
          done: `已保存到 ${target}；后续 AI 可以直接使用这条记忆。`,
        },
        review: {
          title: "先放入待确认",
          effect: `留在待确认区，暂不写入 ${target}。`,
          className: "",
          done: `已放入待确认；它暂时不会写入 ${target}。`,
        },
        archive: {
          title: "不保存到记忆文件",
          effect: `从有效记忆里移除，不写入 ${target}。`,
          className: "warn-action",
          done: `已暂不保留；这条内容不会写入 ${target}。`,
        },
      }[action];
    }

    function renderMemoryDecision(id, action) {
      const copy = memoryDecisionCopy(action);
      return `
        <button class="${copy.className}" data-action="${action}" data-id="${escapeHtml(id)}" title="${escapeHtml(copy.effect)}">
          <span class="action-title">${escapeHtml(copy.title)}</span>
          <span class="action-effect">${escapeHtml(copy.effect)}</span>
        </button>
      `;
    }

    async function updateMemoryItem(id, action) {
      const project = $("project").value.trim() || currentProject || "general";
      const copy = memoryDecisionCopy(action);
      const buttons = [...document.querySelectorAll(`button[data-id="${CSS.escape(id)}"]`)];
      buttons.forEach(button => button.disabled = true);
      $("status").textContent = `正在处理：${copy.title}...`;
      try {
        const data = await api("/api/memory/item", {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({project, id, action})
        });
        renderMemory(data.memory, null);
        $("status").textContent = copy.done;
      } catch (err) {
        buttons.forEach(button => button.disabled = false);
        $("status").textContent = err.message;
      }
    }

    $("load").addEventListener("click", loadSessions);
    $("dream").addEventListener("click", runDream);
    $("refresh").addEventListener("click", refreshMemory);
    $("memoryLanes").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      updateMemoryItem(button.dataset.id, button.dataset.action);
    });
    $("saveSettings").addEventListener("click", saveSettings);
    $("quitBtn").addEventListener("click", quitApp);
    $("tabExtract").addEventListener("click", () => showPanel("extract"));
    $("tabSettings").addEventListener("click", () => showPanel("settings"));
    loadConfig().then(loadSessions).catch(err => $("status").textContent = err.message);
  </script>
</body>
</html>
"""
