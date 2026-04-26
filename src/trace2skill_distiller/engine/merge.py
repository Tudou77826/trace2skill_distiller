"""Step 3: Write topic-based skill files in Claude Code SKILL.md format."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..llm import LLMClient
from ..models import TopicSkill, SkillRule


MERGE_SYSTEM = """你是一个 Skill 仓库管理员，负责将新蒸馏的规则合并到已有的技能文件中。
直接输出合并后的完整 Markdown 内容（包含 YAML frontmatter）。"""

MERGE_PROMPT = """## 当前技能文件内容
{current_content}

## 待合并的新规则
{new_rules_json}

## 合并规则
1. **去重**: 语义相同的规则只保留一条，保留 confidence 最高的版本
2. **冲突检测**: 如果新旧规则矛盾，保留 confidence 更高的
3. **优先级排序**: ALWAYS > WHEN_THEN > NEVER > AVOID
4. **数量控制**: 最多保留 {max_rules} 条规则
5. **保持格式**: 输出格式与原文件一致（包含 YAML frontmatter）
6. **更新 description**: 如果新规则扩展了技能的适用范围，更新 frontmatter 中的 description

## 输出
直接输出完整的 Markdown 文件内容（从 --- 开始），不要省略任何部分。"""


def write_topic_skill(
    result: TopicSkill,
    output_dir: Path,
    project: str,
) -> Path:
    """Write a TopicSkill to <topic-id>/SKILL.md (first-time write, no LLM needed)."""
    project_dir = Path(output_dir).expanduser() / project
    skill_dir = project_dir / result.topic_id
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = _format_skill_markdown(result)
    path = skill_dir / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def merge_topic_skill(
    existing_path: Path,
    new_result: TopicSkill,
    fast_llm: LLMClient,
    max_rules: int = 15,
) -> Path:
    """Merge new distillation results into an existing SKILL.md file."""
    current_content = existing_path.read_text(encoding="utf-8")

    rules_data = [r.model_dump() for r in new_result.rules]
    new_rules_json = json.dumps(rules_data, ensure_ascii=False, indent=2)

    # Truncate if too long
    if len(new_rules_json) > 6000:
        new_rules_json = new_rules_json[:6000] + "\n...[truncated]"

    merged = fast_llm.chat(
        MERGE_SYSTEM,
        MERGE_PROMPT.format(
            current_content=current_content,
            new_rules_json=new_rules_json,
            max_rules=max_rules,
        ),
        temperature=0.2,
        max_tokens=4096,
    )

    existing_path.write_text(merged, encoding="utf-8")
    return existing_path


def write_or_merge_topic(
    result: TopicSkill,
    output_dir: Path,
    project: str,
    fast_llm: LLMClient | None = None,
    max_rules: int = 15,
) -> Path:
    """Write a new topic file or merge into existing one."""
    project_dir = Path(output_dir).expanduser() / project
    existing_path = project_dir / result.topic_id / "SKILL.md"

    if existing_path.exists() and fast_llm:
        return merge_topic_skill(existing_path, result, fast_llm, max_rules)
    else:
        return write_topic_skill(result, output_dir, project)


def write_index(
    skills: list[TopicSkill],
    output_dir: Path,
    project: str,
) -> Path:
    """Write an _index.md listing all topic skills with summaries."""
    project_dir = Path(output_dir).expanduser() / project
    project_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# {project} — 技能索引",
        "",
        f"共 {len(skills)} 个技能主题 | 更新时间: {today}",
        "",
    ]

    for skill in skills:
        rule_count = len(skill.rules)
        lines.append(f"## [{skill.skill_title}]({skill.topic_id}/SKILL.md)")
        lines.append("")
        lines.append(f"{skill.description}")
        lines.append("")
        lines.append(f"- 规则数: {rule_count}")
        lines.append(f"- 来源会话: {len(skill.source_sessions)} 个")
        lines.append("")

    lines.append("---")
    lines.append("由 trace2skill-distiller 自动生成")

    path = project_dir / "_index.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _sanitize_name(name: str) -> str:
    """Normalize a name for YAML frontmatter: lowercase, hyphens only."""
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "unnamed-skill"


def _format_skill_markdown(result: TopicSkill) -> str:
    """Format a TopicSkill as a Claude Code SKILL.md with YAML frontmatter."""
    today = datetime.now().strftime("%Y-%m-%d")
    skill_name = _sanitize_name(result.topic_id)
    description = (result.description or result.summary or result.skill_title)[:1024]

    # Build the "必须遵循" section — always rules
    always_rules = [r for r in result.rules if r.type == "ALWAYS"]
    when_then_rules = [r for r in result.rules if r.type == "WHEN_THEN"]
    never_rules = [r for r in result.rules if r.type == "NEVER"]
    avoid_rules = [r for r in result.rules if r.type == "AVOID"]

    # YAML frontmatter
    lines = [
        "---",
        f"name: {skill_name}",
        f"description: {description}",
        "---",
        "",
        f"# {result.skill_title}",
        "",
        result.summary,
        "",
    ]

    # Source metadata
    lines.extend([
        "## 来源",
        "",
        f"- 主题: {result.topic_name}",
        f"- 分析会话: {len(result.source_sessions)} 个",
        f"- 更新时间: {today}",
        "",
    ])

    # ALWAYS section
    if always_rules:
        lines.append("## 必须遵循")
        lines.append("")
        for r in always_rules:
            lines.append(f"- **ALWAYS**: {r.action}")
            if r.evidence_from_success:
                lines.append(f"  - 证据: {r.evidence_from_success[0][:80]}")
        lines.append("")

    # WHEN/THEN section
    if when_then_rules:
        lines.append("## 条件规则")
        lines.append("")
        for r in when_then_rules:
            lines.append(f"- **WHEN** {r.condition} **THEN** {r.action}")
        lines.append("")

    # Lessons learned section
    if never_rules or avoid_rules:
        lines.append("## 经验教训")
        lines.append("")
        for r in never_rules:
            lines.append(f"- **NEVER**: {r.action}")
            if r.evidence_from_failure:
                lines.append(f"  - 教训: {r.evidence_from_failure[0][:80]}")
        for r in avoid_rules:
            lines.append(f"- **AVOID**: {r.action}")
        lines.append("")

    # Changelog
    lines.append("## 变更记录")
    lines.append("")
    lines.append(f"- {today}: 初始创建，从 {len(result.source_sessions)} 个会话中提炼")

    return "\n".join(lines)


def save_trajectories(
    trajectories: list,
    output_dir: Path,
    project: str,
) -> Path:
    """Save trajectory summaries as JSON for future reference."""
    from ..models import TrajectorySummary

    traj_dir = Path(output_dir).expanduser() / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    data = [t.model_dump() for t in trajectories]
    today = datetime.now().strftime("%Y-%m-%d")
    path = traj_dir / f"{project}_{today}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path
