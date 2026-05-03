"""Knowledge MD formatter — single file, topics with knowledge points."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...analysis.types import TopicSkill


def write_knowledge(
    skills: list[TopicSkill],
    output_dir: Path,
    project: str,
) -> Path:
    """Write a single knowledge collection MD file."""
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    total_rules = sum(len(s.rules) for s in skills)
    total_sessions = sum(len(s.source_sessions) for s in skills)

    lines = [
        f"# Knowledge — {project}",
        "",
        f"> {today} | {len(skills)} topics | {total_rules} points | {total_sessions} sessions",
        "",
    ]

    # TOC
    for i, s in enumerate(skills, 1):
        lines.append(f"{i}. **{s.skill_title}** ({len(s.rules)} points)")
    lines.append("")

    for s in skills:
        lines.append("---")
        lines.append("")
        lines.append(f"## {s.skill_title}")
        lines.append("")

        if s.summary:
            lines.append(s.summary)
            lines.append("")

        if not s.rules:
            lines.append("*(no knowledge points)*")
            lines.append("")
            continue

        for r in s.rules:
            conf = f"{r.confidence * 100:.0f}%"
            scope = f" | {r.scope}" if r.scope and r.scope != "general" else ""
            line = f"- **[{r.type}]** {r.action} — {conf}{scope}"
            if r.condition:
                line += f" (when: {r.condition})"
            if r.evidence_from_success:
                line += f" ← {r.evidence_from_success[0]}"
            if r.evidence_from_failure:
                line += f" ✗ {r.evidence_from_failure[0]}"
            lines.append(line)

        if s.body:
            lines.append("")
            lines.append(s.body)

        lines.append("")

    lines.append("---")

    path = output_dir / f"{project}_knowledge.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
