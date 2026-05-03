"""Semantic clustering strategy using LLM."""

from __future__ import annotations

import re
from pathlib import Path

from ...llm import LLMClient
from ...mining.types import TrajectorySummary
from ..types import TopicCluster, ClusteringResult

CLUSTER_SYSTEM = """你是一个开发主题分类器。分析以下开发会话的摘要，将它们按技术主题分组。"""

CLUSTER_PROMPT = """## 会话列表
{descriptors}

## 分组规则
1. 同一主题的会话应该解决相似的技术问题或实现相似的功能
2. 每个主题至少包含 {min_size} 个会话
3. 最多 {max_topics} 个主题
4. 一个会话可以属于多个主题（如果它涉及多个领域）
5. 无法归类的会话放入 unclustered
6. 即使只有少量会话，也要尽量按主题分组。如果所有会话都讲同一件事，归为 1 个主题即可
7. 不要因为会话少就把所有会话都放入 unclustered

## 已有主题（优先归入）
{existing_topics_section}

## 白名单保护的 topic_id（不可修改，只能追加新会话）
{protected_section}

## 输出格式（严格 JSON）
{{
  "clusters": [
    {{
      "topic_id": "short-english-slug",
      "topic_name": "中文主题名称",
      "topic_summary": "1-2句话描述这个主题涵盖的内容",
      "session_ids": ["s0", "s3"],
      "primary_project": "项目名"
    }}
  ],
  "unclustered": ["s12"]
}}

重要:
1. topic_id 必须是简短的小写英文 slug（2-4个单词，用连字符连接），如 "jwt-auth"、"redis-debug"、"project-setup"。不要使用中文或长句子。
2. session_ids 和 unclustered 中必须使用会话编号（s0, s1, s2...），对应上面的编号。"""


class SemanticClusterStrategy:
    """LLM-based semantic clustering."""

    def __init__(self, llm: LLMClient, output_dir: Path | None = None, project: str = ""):
        self._llm = llm
        self._output_dir = output_dir
        self._project = project

    def cluster(
        self,
        trajectories: list[TrajectorySummary],
        min_size: int = 2,
        max_topics: int = 8,
        existing_topics: dict[str, str] | None = None,
        protected_topics: list[str] | None = None,
    ) -> ClusteringResult:
        if not trajectories:
            return ClusteringResult()

        effective_min_size = min(min_size, len(trajectories))
        descriptors = _build_descriptors(trajectories)

        existing = existing_topics or {}
        if not existing and self._output_dir:
            existing = _scan_existing_topics(self._output_dir, self._project)

        if existing:
            existing_section = "已存在以下主题文件，优先将新会话归入:\n"
            for topic_id, summary in existing.items():
                lock = " [保护]" if topic_id in (protected_topics or []) else ""
                existing_section += f"  - {topic_id}: {summary}{lock}\n"
        else:
            existing_section = "（无已有主题）"

        protected_section = ", ".join(protected_topics or []) or "（无）"

        result = self._llm.chat_json_with_retry(
            CLUSTER_SYSTEM,
            CLUSTER_PROMPT.format(
                descriptors=descriptors,
                min_size=effective_min_size,
                max_topics=max_topics,
                existing_topics_section=existing_section,
                protected_section=protected_section,
            ),
            temperature=0.2,
            max_tokens=4096,
        )

        alias_map = {f"s{i}": t.session_id for i, t in enumerate(trajectories)}
        real_ids = {t.session_id for t in trajectories}

        def _resolve_ids(raw_ids: list) -> list[str]:
            resolved = []
            for sid in raw_ids:
                if sid in alias_map:
                    resolved.append(alias_map[sid])
                elif sid in real_ids:
                    resolved.append(sid)
            return resolved

        clusters = []
        for c in result.get("clusters", []):
            topic_id = c.get("topic_id", "")
            if not topic_id:
                continue
            topic_id = _make_slug(topic_id)
            session_ids = _resolve_ids(c.get("session_ids", []))
            if not session_ids:
                continue
            clusters.append(TopicCluster(
                topic_id=topic_id,
                topic_name=c.get("topic_name", topic_id),
                topic_summary=c.get("topic_summary", ""),
                session_ids=session_ids,
                primary_project=c.get("primary_project", self._project),
            ))

        if protected_topics:
            protected_set = set(protected_topics)
            returned_ids = {c.topic_id for c in clusters}
            for pid in protected_set - returned_ids:
                if pid in existing:
                    clusters.append(TopicCluster(
                        topic_id=pid,
                        topic_name=pid,
                        topic_summary=existing[pid],
                        session_ids=[],
                        primary_project=self._project,
                    ))

        unclustered_raw = _resolve_ids(result.get("unclustered", []))
        clustered_ids: set[str] = set()
        for c in clusters:
            clustered_ids.update(c.session_ids)
        unclustered = [t.session_id for t in trajectories if t.session_id not in clustered_ids]

        return ClusteringResult(clusters=clusters, unclustered=unclustered)


def _build_descriptors(trajectories: list[TrajectorySummary]) -> str:
    lines = []
    for i, t in enumerate(trajectories):
        problems = "; ".join(p.problem for p in t.problems_encountered[:3]) if t.problems_encountered else ""
        decisions = "; ".join(d.decision for d in t.key_decisions[:3]) if t.key_decisions else ""
        lessons = "; ".join(t.lessons_learned[:3]) if t.lessons_learned else ""

        parts = [f"s{i}: {t.session_id}"]
        if t.project:
            parts.append(f"project={t.project}")
        parts.append(f"type={t.session_type}")
        parts.append(f"intent={t.intent[:80]}")
        parts.append(f"result={t.label}")
        if problems:
            parts.append(f"problems={problems[:100]}")
        if decisions:
            parts.append(f"decisions={decisions[:100]}")
        if lessons:
            parts.append(f"lessons={lessons[:100]}")

        lines.append(" | ".join(parts))

    return "\n".join(lines)


def _scan_existing_topics(output_dir: Path | None, project: str) -> dict[str, str]:
    if not output_dir or not project:
        return {}

    project_dir = Path(output_dir).expanduser() / project
    if not project_dir.exists():
        return {}

    existing = {}
    for skill_dir in project_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        topic_id = skill_dir.name
        try:
            text = skill_file.read_text(encoding="utf-8")
            summary = _extract_description(text)
            existing[topic_id] = summary or topic_id
        except Exception:
            existing[topic_id] = topic_id

    return existing


def _extract_description(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end < 0:
        return ""
    frontmatter = text[3:end]
    for line in frontmatter.split("\n"):
        line = line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip()[:100]
    return ""


def _make_slug(text: str) -> str:
    slug = text.strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > 40:
        slug = slug[:40].rstrip("-")
    if not slug:
        slug = "unnamed-topic"
    return slug
