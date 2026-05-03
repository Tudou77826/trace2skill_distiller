"""Analysis data types — clustering and distillation outputs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillRule(BaseModel):
    """A single distilled skill rule."""
    id: str = ""
    type: str = ""  # ALWAYS | WHEN_THEN | NEVER | AVOID
    condition: str = ""
    action: str = ""
    evidence_from_success: list[str] = Field(default_factory=list)
    evidence_from_failure: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    scope: str = "general"  # general | project-specific | language-specific


class TopicCluster(BaseModel):
    """A group of trajectories that share a common topic."""
    topic_id: str          # slug: "jwt-auth"
    topic_name: str        # "JWT 认证配置"
    topic_summary: str     # 1-2 句描述
    session_ids: list[str] = Field(default_factory=list)
    primary_project: str = ""


class TopicSkill(BaseModel):
    """Output of distilling a single topic cluster."""
    topic_id: str
    topic_name: str
    skill_title: str       # "JWT 认证配置技能"
    skill_type: str = "checklist"
    description: str       # English, with trigger words
    summary: str           # 一段概述
    rules: list[SkillRule] = Field(default_factory=list)
    body: str = ""         # Markdown body
    source_sessions: list[str] = Field(default_factory=list)


class ClusteringResult(BaseModel):
    """Output of the topic clustering step."""
    clusters: list[TopicCluster] = Field(default_factory=list)
    unclustered: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Full output of the analysis layer."""
    clustering: ClusteringResult
    skills: list[TopicSkill]
