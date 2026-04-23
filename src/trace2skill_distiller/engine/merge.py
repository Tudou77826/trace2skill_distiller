"""Step 3: Merge skill patches into SKILL.md files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..llm import LLMClient
from ..models import SkillPatch, SkillRule


MERGE_SYSTEM = """你是一个 Skill 仓库管理员，负责合并来自不同分析维度的 Skill 补丁。
直接输出合并后的 Markdown 内容（SKILL.md 格式）。"""

MERGE_PROMPT = """## 当前 Skill 文件
{current_skill}

## 待合并的新规则补丁
{patches_json}

## 合并规则
1. **去重**: 语义相同的规则只保留一条，保留 confidence 最高的版本
2. **冲突检测**: 如果新旧规则矛盾，保留 confidence 更高的
3. **优先级排序**: ALWAYS > WHEN_THEN > NEVER > AVOID
4. **数量控制**: 最多保留 {max_rules} 条规则，移除 confidence 最低的
5. **分类归档**: 按 scope 分组

## 输出格式
直接输出完整的 SKILL.md 内容，格式：

# Skill: {{skill_name}}

## 概述
一行描述这个 Skill 的用途。

## 通用规则 (General)
- ALWAYS: ...
- WHEN ... THEN: ...

## 项目特定规则 (Project: xxx)
- ALWAYS: ...

## 失败教训 (Lessons Learned)
- NEVER: ...
- AVOID: ...

## 变更记录
- {date}: ..."""


def merge_and_write(
    patches: list[SkillPatch],
    project: str,
    output_dir: Path,
    fast_llm: LLMClient,
    max_rules: int = 15,
) -> Path:
    """Merge all patches into a single SKILL.md file."""

    output_dir.mkdir(parents=True, exist_ok=True)
    skill_path = output_dir / project / "SKILL.md"

    # Read existing skill file if any
    current_skill = ""
    if skill_path.exists():
        current_skill = skill_path.read_text(encoding="utf-8")

    # Serialize patches
    patches_data = []
    for p in patches:
        patches_data.append({
            "dimension": p.dimension,
            "project": p.project,
            "rules": [r.model_dump() for r in p.rules],
        })
    patches_json = json.dumps(patches_data, ensure_ascii=False, indent=2)

    # Truncate if needed
    if len(patches_json) > 6000:
        patches_json = patches_json[:6000] + "\n...[truncated]"

    today = datetime.now().strftime("%Y-%m-%d")

    result = fast_llm.chat(
        MERGE_SYSTEM,
        MERGE_PROMPT.format(
            current_skill=current_skill or "（空文件，首次创建）",
            patches_json=patches_json,
            max_rules=max_rules,
            date=today,
        ),
        temperature=0.2,
        max_tokens=4096,
    )

    # Write skill file
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(result, encoding="utf-8")

    return skill_path


def save_trajectories(
    trajectories: list,
    output_dir: Path,
    project: str,
) -> Path:
    """Save trajectory summaries as JSON for future reference."""
    from ..models import TrajectorySummary

    traj_dir = output_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)

    data = [t.model_dump() for t in trajectories]
    today = datetime.now().strftime("%Y-%m-%d")
    path = traj_dir / f"{project}_{today}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path
