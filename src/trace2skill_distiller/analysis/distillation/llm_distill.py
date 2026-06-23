"""LLM-based skill distillation strategy."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.progress import (
    Progress,
    BarColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ...llm import LLMClient
from ...core.console import console
from ...core.utils import estimate_tokens, truncate_to_token_budget
from ...mining.types import TrajectorySummary
from ..types import TopicCluster, TopicSkill, SkillRule

logger = logging.getLogger(__name__)

DISTILL_SYSTEM = """You are a senior memory curator for human-AI coding sessions.
Do not skim. A session can teach many kinds of reusable memory: user preferences,
standing requirements, repository facts, workflow patterns, knowledge discoveries,
corrections to wrong assumptions, tool/skill feedback, pitfalls, and follow-up
questions. Extract only memories grounded in the supplied trajectories. Prefer a
small number of precise, evidence-backed memories over many generic tips."""

DISTILL_PROMPT = """## Input trajectories

Topic: {topic_name}
Topic summary: {topic_summary}

### Successful / useful trajectories
{t_plus}

### Failed / partial / friction trajectories
{t_minus}

## What to extract

Produce a memory set, not a generic skill article. Each memory must be specific
enough to help the next AI assistant behave better in future sessions.

Use these memory types:
- USER_PREFERENCE: stable user taste, habit, wording, or interaction preference.
- STANDING_REQUIREMENT: a rule the user repeatedly wants followed.
- REPO_FACT: concrete project structure, command, file, API, data shape, or design constraint.
- WORKFLOW_PATTERN: a repeatable way to investigate, implement, test, or ship.
- KNOWLEDGE_DISCOVERY: a non-obvious technical/domain fact discovered during work.
- CORRECTION: a mistaken assumption that was corrected.
- TOOL_FEEDBACK: feedback about an agent skill, plugin, command, model, or tool.
- PITFALL: something that wasted time or caused a bad result.
- OPEN_QUESTION: useful uncertainty that should be verified later.

Quality rules:
- Every memory needs direct evidence from the trajectories. Include the shortest
  useful evidence quote or paraphrase in evidence_from_success or evidence_from_failure.
- Do not write vague advice like "check the config" or "be careful". Name the file,
  command, module, behavior, or user preference.
- Separate durable memory from one-off facts. Put one-off facts in scope
  "project-specific" or lower confidence.
- If a memory is based on criticism or failed output, capture what should change next time.
- Keep action as the reusable memory itself. Use condition only when the memory applies
  in a specific situation.

Return strict JSON:
{{
  "skill_title": "short Chinese title for this memory topic",
  "skill_type": "memory",
  "description": "English trigger description, max 200 chars",
  "summary": "Chinese summary, 1-3 sentences",
  "memory_items": [
    {{
      "id": "memory_001",
      "type": "USER_PREFERENCE|STANDING_REQUIREMENT|REPO_FACT|WORKFLOW_PATTERN|KNOWLEDGE_DISCOVERY|CORRECTION|TOOL_FEEDBACK|PITFALL|OPEN_QUESTION",
      "condition": "when this memory applies, or empty",
      "action": "the concrete reusable memory",
      "evidence_from_success": ["short evidence from successful/useful trajectories"],
      "evidence_from_failure": ["short evidence from failed/partial/friction trajectories"],
      "confidence": 0.0,
      "scope": "general|project-specific|user-specific|repo-specific|tool-specific"
    }}
  ],
  "body": "Optional Markdown with synthesis, contradictions, and follow-up checks"
}}"""


class LLMDistillationStrategy:
    """LLM-based skill distillation."""

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def distill_topic(
        self,
        trajectories: list[TrajectorySummary],
        cluster: TopicCluster,
    ) -> TopicSkill | None:
        cluster_ids = set(cluster.session_ids)
        topic_trajs = [t for t in trajectories if t.session_id in cluster_ids]

        if not topic_trajs:
            return None

        t_plus = [t for t in topic_trajs if t.label == "success"]
        t_minus = [t for t in topic_trajs if t.label in ("failure", "partial")]

        if not t_plus and not t_minus:
            return None

        t_plus_text = _format_trajectories(t_plus)
        t_minus_text = _format_trajectories(t_minus)

        budget = 50000 - estimate_tokens(DISTILL_SYSTEM)
        half_budget = budget // 2
        t_plus_text = truncate_to_token_budget(t_plus_text, half_budget)
        t_minus_text = truncate_to_token_budget(t_minus_text, half_budget)

        result = self._llm.chat_json_with_retry(
            DISTILL_SYSTEM,
            DISTILL_PROMPT.format(
                topic_name=cluster.topic_name,
                topic_summary=cluster.topic_summary or cluster.topic_name,
                t_plus=t_plus_text or "(none)",
                t_minus=t_minus_text or "(none)",
            ),
            temperature=0.3,
            max_tokens=8192,
        )

        raw_rules = result.get("memory_items") or result.get("rules", [])
        rules = []
        for r in raw_rules:
            rules.append(
                SkillRule(
                    id=r.get("id", f"rule_{len(rules)}"),
                    type=r.get("type", ""),
                    condition=r.get("condition", ""),
                    action=r.get("action", ""),
                    evidence_from_success=r.get("evidence_from_success", []),
                    evidence_from_failure=r.get("evidence_from_failure", []),
                    confidence=r.get("confidence", 0.5),
                    scope=r.get("scope", "general"),
                )
            )

        return TopicSkill(
            topic_id=cluster.topic_id,
            topic_name=cluster.topic_name,
            skill_title=result.get("skill_title", cluster.topic_name),
            skill_type=result.get("skill_type", "memory"),
            description=result.get("description", ""),
            summary=result.get("summary", cluster.topic_summary),
            rules=rules,
            body=result.get("body", ""),
            source_sessions=cluster.session_ids,
        )

    def distill_all(
        self,
        trajectories: list[TrajectorySummary],
        clusters: list[TopicCluster],
        *,
        max_workers: int = 1,
    ) -> list[TopicSkill]:
        skills: list[TopicSkill] = []

        # Simple path for single cluster — no progress bar overhead
        if len(clusters) <= 1:
            for cluster in clusters:
                try:
                    skill = self.distill_topic(trajectories, cluster)
                    if skill and (skill.rules or skill.body):
                        skills.append(skill)
                except Exception as e:
                    logger.warning("Topic %s failed: %s", cluster.topic_name, e)
            return skills

        desc = f"Distilling [{max_workers}w]" if max_workers > 1 else "Distilling"

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(desc, total=len(clusters))

            if max_workers <= 1:
                for cluster in clusters:
                    try:
                        skill = self.distill_topic(trajectories, cluster)
                        if skill and (skill.rules or skill.body):
                            skills.append(skill)
                    except Exception as e:
                        logger.warning("Topic %s failed: %s", cluster.topic_name, e)
                    progress.advance(task)
            else:
                logger.info("Starting parallel distillation with %d workers", max_workers)
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {}
                    for cluster in clusters:
                        future = pool.submit(self.distill_topic, trajectories, cluster)
                        futures[future] = cluster.topic_name

                    for future in as_completed(futures):
                        topic_name = futures[future]
                        try:
                            skill = future.result()
                            if skill and (skill.rules or skill.body):
                                skills.append(skill)
                        except Exception as e:
                            logger.warning("Topic %s failed: %s", topic_name, e)
                        progress.advance(task)

        logger.info(
            "Distillation done: %d/%d topics produced skills",
            len(skills), len(clusters),
        )
        return skills


def _format_trajectories(trajectories: list[TrajectorySummary]) -> str:
    parts = []
    for t in trajectories:
        entry = f"### Session: {t.session_id}\n"
        entry += f"Type: {t.session_type} | Label: {t.label}\n"
        entry += f"Intent: {t.intent}\n"

        if t.what_happened:
            entry += "Phases:\n"
            for phase in t.what_happened:
                entry += f"  - {phase.phase}: {phase.summary}\n"

        if t.problems_encountered:
            entry += "Problems:\n"
            for p in t.problems_encountered:
                entry += f"  - {p.problem} -> {p.how_resolved}\n"

        if t.key_decisions:
            entry += "Decisions:\n"
            for d in t.key_decisions:
                entry += f"  - {d.decision} (reason: {d.rationale})\n"

        if t.lessons_learned:
            entry += "Lessons: " + "; ".join(t.lessons_learned) + "\n"

        if t.discoveries:
            entry += "Discoveries:\n"
            for d in t.discoveries:
                entry += f"  - {d}\n"

        parts.append(entry)

    return "\n---\n".join(parts)
