"""Generate HTML distillation report."""

from __future__ import annotations

import html as html_mod
from datetime import datetime
from pathlib import Path

from ..models import DistillReport

# Threshold for success label color
_SCORE_HIGH = 0.7
_SCORE_MID = 0.4

# ── Label translations ──
_LABEL_ZH = {"success": "成功", "partial": "部分", "failure": "失败"}


def generate_report(report: DistillReport, output_path: Path | None = None) -> str:
    """Generate a self-contained HTML report. Returns HTML string."""
    rendered = _TEMPLATE.format(
        report_id=html_mod.escape(report.run_id or "N/A"),
        project=html_mod.escape(report.project or "all"),
        started=html_mod.escape(report.started_at or "—"),
        finished=html_mod.escape(report.finished_at or "—"),
        duration=_fmt_duration(report.total_duration_seconds),
        sessions_total=report.sessions_total,
        sessions_passed=report.sessions_passed_filter,
        label_counts=_label_counts(report),
        topics_found=report.topics_found,
        unclustered=report.unclustered_count,
        total_rules=report.total_rules,
        session_table=_render_session_table(report),
        topic_cards=_render_topic_cards(report),
        timeline=_render_timeline(report),
        llm_usage=_render_llm_usage(report),
        output_files=_render_output_files(report),
        errors=_render_errors(report),
        summary_stats=_render_summary_stats(report),
        nav_items=_render_nav_items(report),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")

    return rendered


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    m, s = divmod(seconds, 60)
    return f"{int(m)} 分 {s:.0f} 秒"


def _label_zh(label: str) -> str:
    return _LABEL_ZH.get(label, label)


def _label_counts(report: DistillReport) -> str:
    s = sum(1 for x in report.sessions if x.label == "success")
    p = sum(1 for x in report.sessions if x.label == "partial")
    f = sum(1 for x in report.sessions if x.label == "failure")
    return (
        f'<span class="label-success">{s} 成功</span>'
        f' &middot; <span class="label-partial">{p} 部分</span>'
        f' &middot; <span class="label-failure">{f} 失败</span>'
    )


def _score_color(score: float) -> str:
    if score >= _SCORE_HIGH:
        return "var(--success)"
    if score >= _SCORE_MID:
        return "var(--warning)"
    return "var(--danger)"


def _render_session_table(report: DistillReport) -> str:
    if not report.sessions:
        return '<p class="empty">无会话数据</p>'

    rows = []
    for s in report.sessions:
        score_clamped = max(0.0, min(s.label_score, 1.0))
        score_pct = f"{score_clamped * 100:.0f}%"
        score_col = _score_color(score_clamped)
        label_cls = f"badge badge-{s.label}" if s.label in ("success", "partial", "failure") else "badge"
        label_text = _label_zh(s.label)
        # Show reason for non-success sessions
        reason_html = ""
        if s.label_reason:
            reason_html = f'<div class="label-reason">{html_mod.escape(s.label_reason)}</div>'
        rows.append(
            f"<tr>"
            f'<td class="mono" title="{html_mod.escape(s.session_id)}">{html_mod.escape(s.session_id[:12])}</td>'
            f"<td>{html_mod.escape(s.project)}</td>"
            f"<td>{html_mod.escape(s.intent[:50])}</td>"
            f"<td>{s.msg_count}</td>"
            f"<td>{s.tool_count}</td>"
            f'<td><span class="{label_cls}">{html_mod.escape(label_text)}</span>{reason_html}</td>'
            f'<td><div class="score-bar" style="--fill:{score_pct};--color:{score_col}">'
            f'<div class="score-fill"></div></div><span class="score-text">{score_pct}</span></td>'
            f"</tr>"
        )

    return (
        '<table class="data-table"><thead><tr>'
        "<th>会话 ID</th><th>项目</th><th>意图</th>"
        "<th>消息数</th><th>工具数</th><th>标签</th><th>评分</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_topic_cards(report: DistillReport) -> str:
    if not report.topics:
        return '<p class="empty">未发现主题</p>'

    cards = []
    for t in report.topics:
        rule_items = []
        for r in t.rules:
            conf_pct = f"{r.confidence * 100:.0f}%"
            conf_col = _score_color(r.confidence)
            type_cls = f"rule-type rule-type-{r.type.lower().replace('_', '-')}"
            rule_items.append(
                f'<div class="rule-item">'
                f'<span class="{type_cls}">{html_mod.escape(r.type)}</span> '
                f'<span class="rule-action">{html_mod.escape(r.action[:80])}</span>'
                f'<div class="mini-bar"><div class="mini-fill" style="width:{conf_pct};background:{conf_col}"></div></div>'
                f"</div>"
            )

        rules_html = "".join(rule_items) if rule_items else '<span class="empty">无规则</span>'

        cards.append(
            f'<div class="topic-card" id="topic-{html_mod.escape(t.topic_id)}">'
            f'<div class="topic-header">'
            f'<h3>{html_mod.escape(t.topic_name)}</h3>'
            f'<span class="badge">{t.session_count} 个会话</span>'
            f'<span class="badge">{t.rule_count} 条规则</span>'
            f"</div>"
            f'<p class="topic-summary">{html_mod.escape(t.topic_summary)}</p>'
            f'<div class="topic-rules">{rules_html}</div>'
            f"</div>"
        )

    return "".join(cards)


def _render_timeline(report: DistillReport) -> str:
    if not report.steps:
        return '<p class="empty">无计时数据</p>'

    max_dur = max((s.duration_seconds for s in report.steps), default=1) or 1
    items = []
    for s in report.steps:
        pct = (s.duration_seconds / max_dur) * 100
        items.append(
            f'<div class="timeline-row">'
            f'<span class="timeline-name">{html_mod.escape(s.name)}</span>'
            f'<div class="timeline-bar-bg"><div class="timeline-bar" style="width:{pct:.0f}%"></div></div>'
            f'<span class="timeline-dur">{_fmt_duration(s.duration_seconds)}</span>'
            f"</div>"
        )
    return "".join(items)


def _render_llm_usage(report: DistillReport) -> str:
    if not report.llm_usage:
        return '<p class="empty">无模型调用数据</p>'

    rows = []
    for u in report.llm_usage:
        total = u.input_tokens + u.output_tokens
        rows.append(
            f"<tr>"
            f"<td>{html_mod.escape(u.label)}</td>"
            f"<td>{u.calls}</td>"
            f"<td>{u.input_tokens:,}</td>"
            f"<td>{u.output_tokens:,}</td>"
            f"<td><strong>{total:,}</strong></td>"
            f"</tr>"
        )

    return (
        '<table class="data-table compact"><thead><tr>'
        "<th>模型</th><th>调用次数</th><th>输入 Token</th><th>输出 Token</th><th>合计</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_output_files(report: DistillReport) -> str:
    paths = [t.output_path for t in report.topics if t.output_path]
    if not paths:
        return '<p class="empty">无输出文件</p>'

    items = [f'<li><code>{html_mod.escape(p)}</code></li>' for p in paths]
    return '<ul class="file-list">' + "".join(items) + "</ul>"


def _render_errors(report: DistillReport) -> str:
    if not report.errors:
        return ""
    items = [f"<li>{html_mod.escape(e)}</li>" for e in report.errors]
    return '<div class="error-box"><h4>错误与警告</h4><ul>' + "".join(items) + "</ul></div>"


def _render_summary_stats(report: DistillReport) -> str:
    total_tokens = sum(u.input_tokens + u.output_tokens for u in report.llm_usage)
    total_calls = sum(u.calls for u in report.llm_usage)
    return (
        f'<div class="stat-grid">'
        f'<div class="stat-card"><div class="stat-value">{len(report.sessions)}</div><div class="stat-label">已分析会话</div></div>'
        f'<div class="stat-card"><div class="stat-value">{report.topics_found}</div><div class="stat-label">发现主题</div></div>'
        f'<div class="stat-card"><div class="stat-value">{report.total_rules}</div><div class="stat-label">提取规则</div></div>'
        f'<div class="stat-card"><div class="stat-value">{total_calls}</div><div class="stat-label">模型调用</div></div>'
        f'<div class="stat-card"><div class="stat-value">{total_tokens:,}</div><div class="stat-label">总 Token 数</div></div>'
        f'<div class="stat-card"><div class="stat-value">{_fmt_duration(report.total_duration_seconds)}</div><div class="stat-label">耗时</div></div>'
        f"</div>"
    )


def _render_nav_items(report: DistillReport) -> str:
    items = [
        '<a href="#summary">概览</a>',
        '<a href="#sessions">会话</a>',
        '<a href="#topics">主题</a>',
        '<a href="#timeline">流水线</a>',
        '<a href="#usage">模型调用</a>',
        '<a href="#output">输出</a>',
    ]
    return "".join(items)


# ── HTML Template ──

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trace2Skill 蒸馏报告 — {project}</title>
<style>
:root {{
  --bg: #faf8f5;
  --surface: #ffffff;
  --surface2: #f3f0eb;
  --border: #e8e3db;
  --text: #2d2a26;
  --text2: #8a847a;
  --primary: #b87333;
  --primary-light: #d4944a;
  --primary-bg: rgba(184,115,51,0.08);
  --success: #5b9a6f;
  --success-bg: rgba(91,154,111,0.10);
  --warning: #c78c0a;
  --warning-bg: rgba(199,140,10,0.10);
  --danger: #c45454;
  --danger-bg: rgba(196,84,84,0.10);
  --partial: #d4873a;
  --partial-bg: rgba(212,135,58,0.10);
  --radius: 10px;
  --shadow: 0 1px 3px rgba(45,42,38,0.06), 0 1px 2px rgba(45,42,38,0.04);
  --shadow-hover: 0 4px 12px rgba(45,42,38,0.08);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.7;
  padding: 0;
}}
.container {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}

/* Header */
.report-header {{
  background: linear-gradient(135deg, #fff7ed 0%, #fef3e2 50%, #fdf6ee 100%);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 32px;
  margin-bottom: 32px;
  box-shadow: var(--shadow);
}}
.report-header h1 {{
  font-size: 26px;
  font-weight: 700;
  margin-bottom: 10px;
  color: var(--text);
}}
.report-header h1 span {{ color: var(--primary); }}
.report-meta {{
  color: var(--text2);
  font-size: 14px;
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}}
.report-meta span {{
  display: inline-flex;
  align-items: center;
}}
.report-meta span::before {{
  content: '';
  display: inline-block;
  width: 4px; height: 4px;
  background: var(--primary);
  border-radius: 50%;
  margin-right: 8px;
  opacity: 0.5;
}}

/* Nav */
.nav {{
  display: flex;
  gap: 4px;
  margin-bottom: 32px;
  overflow-x: auto;
  padding-bottom: 4px;
  background: var(--surface);
  border-radius: var(--radius);
  padding: 6px;
  box-shadow: var(--shadow);
}}
.nav a {{
  color: var(--text2);
  text-decoration: none;
  padding: 8px 18px;
  border-radius: 8px;
  font-size: 14px;
  white-space: nowrap;
  transition: all 0.2s;
}}
.nav a:hover {{
  color: var(--primary);
  background: var(--primary-bg);
}}

/* Stat grid */
.stat-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}}
.stat-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  text-align: center;
  box-shadow: var(--shadow);
  transition: box-shadow 0.2s;
}}
.stat-card:hover {{ box-shadow: var(--shadow-hover); }}
.stat-value {{
  font-size: 32px;
  font-weight: 700;
  color: var(--primary);
}}
.stat-label {{
  font-size: 13px;
  color: var(--text2);
  margin-top: 4px;
}}

/* Section */
.section {{
  margin-bottom: 32px;
}}
.section-title {{
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--primary);
  color: var(--text);
}}

/* Data table */
.data-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  background: var(--surface);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
}}
.data-table th {{
  text-align: left;
  padding: 12px 14px;
  color: var(--text2);
  font-weight: 500;
  font-size: 13px;
  background: var(--surface2);
  border-bottom: 1px solid var(--border);
}}
.data-table td {{
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.data-table tbody tr:hover {{ background: var(--primary-bg); }}
.data-table.compact td, .data-table.compact th {{ padding: 9px 12px; }}
.data-table.compact tr:not(:last-child) td {{ border-bottom: 1px solid var(--border); }}

/* Badges */
.badge {{
  display: inline-block;
  padding: 3px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  background: var(--surface2);
  color: var(--text2);
  margin-left: 6px;
}}
.badge-success {{ background: var(--success-bg); color: var(--success); }}
.badge-partial {{ background: var(--partial-bg); color: var(--partial); }}
.badge-failure {{ background: var(--danger-bg); color: var(--danger); }}

/* Score bar */
.score-bar {{
  display: inline-block;
  width: 60px;
  height: 6px;
  background: var(--surface2);
  border-radius: 3px;
  position: relative;
  overflow: hidden;
  vertical-align: middle;
  margin-right: 6px;
}}
.score-fill {{
  position: absolute;
  top: 0; left: 0; bottom: 0;
  width: var(--fill);
  background: var(--color);
  border-radius: 3px;
}}
.score-text {{ font-size: 12px; color: var(--text2); }}

/* Label reason */
.label-reason {{
  font-size: 12px;
  color: var(--text2);
  margin-top: 2px;
  line-height: 1.4;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}

/* Mini bar for rules */
.mini-bar {{
  display: inline-block;
  width: 40px;
  height: 4px;
  background: var(--surface2);
  border-radius: 2px;
  overflow: hidden;
  vertical-align: middle;
  margin-left: 8px;
}}
.mini-fill {{
  height: 100%;
  border-radius: 2px;
}}

/* Topic cards */
.topic-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: var(--shadow);
  transition: box-shadow 0.2s;
}}
.topic-card:hover {{ box-shadow: var(--shadow-hover); }}
.topic-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}}
.topic-header h3 {{
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
}}
.topic-summary {{
  color: var(--text2);
  font-size: 14px;
  margin-bottom: 12px;
}}
.topic-rules {{ padding-left: 4px; }}
.rule-item {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}}
.rule-item:last-child {{ border-bottom: none; }}
.rule-action {{ flex: 1; color: var(--text); }}
.rule-type {{
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 0.3px;
  white-space: nowrap;
}}
.rule-type-always {{ background: var(--success-bg); color: var(--success); }}
.rule-type-when-then {{ background: var(--primary-bg); color: var(--primary); }}
.rule-type-never {{ background: var(--danger-bg); color: var(--danger); }}
.rule-type-avoid {{ background: var(--warning-bg); color: var(--warning); }}

/* Timeline */
.timeline-row {{
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}}
.timeline-name {{ width: 160px; font-size: 14px; color: var(--text2); }}
.timeline-bar-bg {{
  flex: 1;
  height: 10px;
  background: var(--surface2);
  border-radius: 5px;
  overflow: hidden;
}}
.timeline-bar {{
  height: 100%;
  background: linear-gradient(90deg, var(--primary), var(--primary-light));
  border-radius: 5px;
  transition: width 0.6s ease;
}}
.timeline-dur {{ width: 80px; font-size: 14px; text-align: right; color: var(--text2); }}

/* File list */
.file-list {{
  list-style: none;
  font-size: 14px;
}}
.file-list li {{
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}}
.file-list code {{
  background: var(--surface2);
  padding: 2px 10px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--primary);
}}

/* Error box */
.error-box {{
  background: var(--danger-bg);
  border: 1px solid rgba(196,84,84,0.25);
  border-radius: var(--radius);
  padding: 16px 20px;
  margin-top: 16px;
}}
.error-box h4 {{ color: var(--danger); margin-bottom: 8px; font-size: 15px; }}
.error-box ul {{ padding-left: 20px; }}
.error-box li {{ font-size: 14px; color: var(--text); }}

/* Empty */
.empty {{ color: var(--text2); font-size: 14px; padding: 12px 0; }}

/* Footer */
.report-footer {{
  text-align: center;
  color: var(--text2);
  font-size: 13px;
  margin-top: 40px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}}

/* Label colors inline */
.label-success {{ color: var(--success); font-weight: 500; }}
.label-partial {{ color: var(--partial); font-weight: 500; }}
.label-failure {{ color: var(--danger); font-weight: 500; }}

/* Animations */
@keyframes fadeUp {{
  from {{ opacity: 0; transform: translateY(12px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
.section {{ animation: fadeUp 0.4s ease both; }}
.section:nth-child(2) {{ animation-delay: 0.05s; }}
.section:nth-child(3) {{ animation-delay: 0.1s; }}
.section:nth-child(4) {{ animation-delay: 0.15s; }}
.section:nth-child(5) {{ animation-delay: 0.2s; }}
.section:nth-child(6) {{ animation-delay: 0.25s; }}

/* Responsive */
@media (max-width: 768px) {{
  .report-meta {{ flex-direction: column; gap: 6px; }}
  .stat-grid {{ grid-template-columns: repeat(3, 1fr); }}
  .timeline-name {{ width: 100px; font-size: 13px; }}
}}
</style>
</head>
<body>
<div class="container">

  <div class="report-header">
    <h1>Trace<span>2</span>Skill 蒸馏报告</h1>
    <div class="report-meta">
      <span>项目：<strong>{project}</strong></span>
      <span>运行：<code>{report_id}</code></span>
      <span>开始：{started}</span>
      <span>结束：{finished}</span>
      <span>耗时：{duration}</span>
    </div>
  </div>

  <nav class="nav">{nav_items}</nav>

  <div id="summary" class="section">
    <div class="section-title">概览</div>
    {summary_stats}
  </div>

  <div id="sessions" class="section">
    <div class="section-title">会话详情（{sessions_passed} / {sessions_total} 通过筛选）&mdash; {label_counts}</div>
    {session_table}
  </div>

  <div id="topics" class="section">
    <div class="section-title">发现主题（{topics_found} 个主题，{unclustered} 个未聚类）</div>
    {topic_cards}
  </div>

  <div id="timeline" class="section">
    <div class="section-title">流水线耗时</div>
    {timeline}
  </div>

  <div id="usage" class="section">
    <div class="section-title">模型调用</div>
    {llm_usage}
  </div>

  <div id="output" class="section">
    <div class="section-title">输出文件</div>
    {output_files}
  </div>

  {errors}

  <div class="report-footer">
    由 trace2skill-distiller 自动生成 &middot; {generated_at}
  </div>
</div>
</body>
</html>"""
