"""Backend services for the GUI.

These pure-Python helpers (config snapshot/save, session listing, memory
snapshot, memory review decisions, localization) used to live inside the HTTP
handler in ``server.py``. They have been extracted here so both the legacy HTTP
UI and the native PySide6 desktop app share one implementation.

Nothing in this module performs network I/O or knows about Qt/HTTP — it is safe
to call from any thread.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.config import (
    DistillConfig,
    LLMConfig,
    init_default_config,
    load_config,
    set_config_value,
)
from ..mining.sources import create_source
from ..output.formatters.memory_md import (
    AGENT_CONTEXT_FILENAME,
    load_memory_store,
    refresh_memory_files,
    summarize_memory_quality,
)

# Source-type label order shown in the config UI.
_SOURCE_OPTIONS = ["opencode", "chrys", "codeagent", "claudecode"]


def session_rows(cfg: DistillConfig, project: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return session rows suitable for the GUI."""
    source = create_source(cfg.source)
    sessions = source.list_sessions(project=project or None)
    sessions.sort(key=lambda s: (s.timestamp, s.msg_count, s.tool_count), reverse=True)
    rows = []
    for session in sessions[:max(1, limit)]:
        tool_count = session.tool_count or source.count_tools(session.id)
        eligible = session.msg_count >= cfg.filter.min_messages and tool_count >= cfg.filter.min_tools
        blocked_by = []
        if session.msg_count < cfg.filter.min_messages:
            blocked_by.append(f"消息少于 {cfg.filter.min_messages}")
        if tool_count < cfg.filter.min_tools:
            blocked_by.append(f"工具少于 {cfg.filter.min_tools}")
        rows.append({
            "id": session.id,
            "title": session.title or "（未命名）",
            "project": session.project or "",
            "messages": session.msg_count,
            "tools": tool_count,
            "date": _format_timestamp(session.timestamp),
            "eligible": eligible,
            "hint": "，".join(blocked_by),
        })
    return rows


def memory_snapshot(cfg: DistillConfig, project: str) -> dict[str, Any]:
    """Return current memory quality and top review items for a project."""
    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    quality = _localize_quality(summarize_memory_quality(store))
    review_items = _top_review_items(store, limit=8)
    learned_items = _learned_items(store, limit=10)
    groups = memory_groups(store)
    # Respect a user-configured custom destination if one is set.
    custom = (cfg.output.agent_context_path or "").strip()
    if custom:
        context_path = Path(custom.replace("{project}", project or "general")).expanduser()
    else:
        context_path = output_dir / project / AGENT_CONTEXT_FILENAME
    return {
        "project": project,
        "quality": quality,
        "summary": _memory_summary(quality),
        "learned_items": learned_items,
        "groups": groups,
        "review_items": review_items,
        "agent_context_exists": context_path.exists(),
        "agent_context_path": str(context_path),
    }


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


def config_view(cfg: DistillConfig) -> dict[str, Any]:
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
            "skill_output_dir": cfg.output.skill_output_dir,
            "agent_context_path": cfg.output.agent_context_path,
            "user_profile_path": cfg.output.user_profile_path,
            "repo_facts_path": cfg.output.repo_facts_path,
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


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
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
        "output_skill_output_dir": "output.skill_output_dir",
        "output_agent_context_path": "output.agent_context_path",
        "output_user_profile_path": "output.user_profile_path",
        "output_repo_facts_path": "output.repo_facts_path",
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
    return config_view(load_config())


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


def friendly_error(exc: BaseException) -> str:
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


def run_status_payload(report) -> dict[str, str]:
    """Return a concise GUI status for a finished pipeline run."""
    if report.sessions_passed_filter == 0:
        return {
            "status": "no_candidates",
            "message": "没有生成结果：所选会话未通过当前筛选条件。",
            "action": "请选择消息数和工具数足够的会话，或在「设置」里降低筛选阈值后重试。",
        }
    if report.total_rules == 0:
        return {
            "status": "no_rules",
            "message": "没有生成记忆：会话已处理，但模型没有提取出可复用规则。",
            "action": "换一组更完整、包含明确问题和解决过程的会话再试；也可以检查模型配置是否可用。",
        }
    return {
        "status": "ok",
        "message": (
            f"提取完成：处理 {report.sessions_passed_filter} 个会话，"
            f"发现 {report.topics_found} 个主题，生成 {report.total_rules} 条记忆。"
        ),
        "action": "查看下方待复审内容；确认有证据支撑的记忆后，它们会进入智能体上下文。",
    }


def update_memory_item(cfg: DistillConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a human review action to one memory item and regenerate artifacts."""
    project = str(payload.get("project") or "general").strip() or "general"
    item_id = str(payload.get("id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not item_id:
        raise ValueError("缺少记忆 ID。")
    if action not in {"confirm", "archive", "review"}:
        raise ValueError("未知操作。")

    output_dir = Path(cfg.output.skill_output_dir).expanduser()
    store = load_memory_store(output_dir, project)
    target = None
    for item in store.get("items", []):
        if item.get("id") == item_id:
            target = item
            break
    if target is None:
        raise ValueError("找不到这条记忆。")

    if action == "confirm":
        target["confirmed"] = True
        target["status"] = "active"
    elif action == "archive":
        target["status"] = "archived"
    elif action == "review":
        target["status"] = "review"
        target["confirmed"] = False

    refresh_memory_files(output_dir, project, store=store, skills=[])
    return memory_snapshot(cfg, project)


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


def memory_groups(store: dict) -> list[dict[str, Any]]:
    """Group memories by the decision a human reviewer needs to make."""
    groups = [
        {
            "id": "keep",
            "title": "建议保留",
            "subtitle": "这些会直接进入后续 AI 上下文，适合长期复用。",
            "items": [],
        },
        {
            "id": "review",
            "title": "需要确认",
            "subtitle": "这些可能有价值，但需要人判断是否准确或是否还缺证据。",
            "items": [],
        },
        {
            "id": "discard",
            "title": "考虑丢弃",
            "subtitle": "这些更像一次性上下文，默认不该占用长期记忆。",
            "items": [],
        },
    ]
    by_id = {group["id"]: group for group in groups}
    for item in store.get("items", []):
        if item.get("status", "active") == "archived":
            continue
        lane = _memory_lane(item)
        by_id[lane]["items"].append(_memory_card(item))
    return groups


def _memory_lane(item: dict) -> str:
    if (
        item.get("status") == "review"
        or item.get("type") == "OPEN_QUESTION"
        or float(item.get("confidence", 0) or 0) < 0.55
        or (not item.get("evidence") and not item.get("confirmed"))
    ):
        return "review"
    if (
        item.get("type") == "REPO_FACT"
        and item.get("scope") == "project-specific"
        and int(item.get("seen_count", 1) or 1) <= 1
        and not item.get("confirmed")
    ):
        return "discard"
    return "keep"


def _learned_items(store: dict, limit: int) -> list[dict[str, Any]]:
    """Return user-facing memory cards for the GUI result panel."""
    active_items = [
        item for item in store.get("items", [])
        if item.get("status", "active") != "archived"
    ]
    active_items.sort(key=lambda item: (
        item.get("status") == "review",
        item.get("type") == "OPEN_QUESTION",
        -float(item.get("confidence", 0) or 0),
        item.get("action", ""),
    ))
    return [_memory_card(item) for item in active_items[:max(0, limit)]]


def _memory_card(item: dict) -> dict[str, Any]:
    status = item.get("status", "active")
    is_review = status == "review" or item.get("type") == "OPEN_QUESTION"
    evidence = [str(x) for x in item.get("evidence", []) if x]
    mem_type = (item.get("type") or "").upper()
    return {
        "id": item.get("id", ""),
        "raw_type": mem_type,
        "type": _memory_type_label(mem_type),
        "status": "需要确认" if is_review else "可直接使用",
        "tone": "review" if is_review else "ready",
        "action": item.get("action", ""),
        "condition": item.get("condition") or "",
        "scope": _scope_label(item.get("scope", "")),
        "confidence": float(item.get("confidence", 0) or 0),
        "evidence": evidence[:2],
        "why": _why_useful(item),
        "ai_use": _ai_use_label(item),
    }


def _memory_summary(quality: dict[str, Any]) -> str:
    total = int(quality.get("total", 0) or 0)
    if not total:
        return "还没有沉淀出可复用记忆。请选择一次信息更完整的会话开始提取。"
    ready = int(quality.get("agent_ready", 0) or 0)
    review = int(quality.get("review", 0) or 0)
    if review:
        return f"本项目已沉淀 {total} 条记忆，其中 {ready} 条可直接用于后续上下文，{review} 条需要你确认。"
    return f"本项目已沉淀 {total} 条记忆，其中 {ready} 条可直接用于后续上下文。"


def _why_useful(item: dict) -> str:
    mem_type = (item.get("type") or "").upper()
    return {
        "USER_PREFERENCE": "以后与用户协作时可以直接调整沟通和执行方式。",
        "STANDING_REQUIREMENT": "这是后续任务必须遵守的稳定要求。",
        "REPO_FACT": "能帮助 AI 在同一项目里更快定位上下文，但需要避免保留一次性细节。",
        "WORKFLOW_PATTERN": "能复用到相似任务，减少重复探索。",
        "KNOWLEDGE_DISCOVERY": "能补充后续分析时需要调用的背景知识。",
        "CORRECTION": "能避免再次沿用已经被纠正的错误假设。",
        "TOOL_FEEDBACK": "能帮助 AI 选择更合适的工具或使用方式。",
        "PITFALL": "能提醒 AI 避开已经踩过的失败路径。",
        "OPEN_QUESTION": "还不能当作事实，需要人确认或后续验证。",
    }.get(mem_type, "可能对后续会话有用，但需要判断是否值得长期保留。")


def _ai_use_label(item: dict) -> str:
    if item.get("status") == "review" or item.get("type") == "OPEN_QUESTION":
        return "暂不直接喂给 AI"
    if item.get("confirmed"):
        return "已确认，可进入长期上下文"
    if float(item.get("confidence", 0) or 0) >= 0.75:
        return "可进入 AI 上下文"
    return "可作为弱提示"


def _scope_label(scope: str) -> str:
    return {
        "general": "通用",
        "project-specific": "当前项目",
        "tool-specific": "工具相关",
        "user-specific": "用户偏好",
    }.get(scope or "", scope or "通用")


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
