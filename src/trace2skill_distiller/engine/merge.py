"""Step 3: Write topic-based skill files in Claude Code SKILL.md format."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..llm import LLMClient
from ..models import TopicSkill


MERGE_SYSTEM = """你是一个 Skill 仓库管理员，负责将新蒸馏的内容合并到已有的技能文件中。
直接输出合并后的完整 Markdown 正文（不含 YAML frontmatter）。"""

MERGE_PROMPT = """## 当前技能正文
{current_body}

## 待合并的新内容
{new_body}

## 合并规则
1. **去重**: 语义相同的内容只保留一条
2. **冲突检测**: 如果新旧内容矛盾，保留更新、更详细的版本
3. **保持格式**: 保持原有的 Markdown 格式风格（标题层级、列表等）
4. **保留 skill_type 格式**: 不要改变技能类型对应的格式结构
5. **更新 description**: 如果新内容扩展了技能的适用范围，输出新的 description
6. **精简**: 合并后去掉冗余内容，保持精炼

## 输出
严格输出 JSON：
{{
  "description": "English description with trigger words (max 200 chars)",
  "body": "合并后的完整 Markdown 正文（不含 YAML frontmatter）"
}}"""


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
    current_body = _extract_body(current_content)

    new_body = new_result.body or ""
    if len(new_body) > 8000:
        new_body = new_body[:8000] + "\n...[truncated]"

    merged = fast_llm.chat_json_with_retry(
        MERGE_SYSTEM,
        MERGE_PROMPT.format(
            current_body=current_body,
            new_body=new_body,
        ),
        temperature=0.2,
        max_tokens=8192,
    )

    merged_body = merged.get("body", current_body)
    merged_description = merged.get("description", new_result.description)

    # Rebuild with updated description
    temp_skill = TopicSkill(
        topic_id=new_result.topic_id,
        topic_name=new_result.topic_name,
        skill_title=new_result.skill_title,
        skill_type=new_result.skill_type,
        description=merged_description,
        summary=new_result.summary,
        body=merged_body,
        source_sessions=new_result.source_sessions,
    )

    existing_path.write_text(_format_skill_markdown(temp_skill), encoding="utf-8")
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
        lines.append(f"- 类型: {skill.skill_type}")
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
    skill_name = _sanitize_name(result.topic_id)
    description = (result.description or result.summary or result.skill_title)[:1024]
    body = result.body or result.summary or ""

    return "\n".join([
        "---",
        f"name: {skill_name}",
        f"description: {description}",
        "---",
        "",
        f"# {result.skill_title}",
        "",
        body,
    ])


def _extract_body(content: str) -> str:
    """Extract Markdown body from a SKILL.md file (strip YAML frontmatter and title)."""
    import re
    # Strip YAML frontmatter
    body = re.sub(r"^---\n.*?\n---\n*", "", content, flags=re.DOTALL)
    # Strip first H1 title
    body = re.sub(r"^#\s+.+\n*", "", body)
    return body.strip()


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
