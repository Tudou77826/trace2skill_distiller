"""Output data types — report models and shaping results."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..analysis.types import SkillRule


class SessionEntry(BaseModel):
    """A single session in the report."""
    session_id: str
    title: str = ""
    project: str = ""
    msg_count: int = 0
    tool_count: int = 0
    label: str = ""
    label_score: float = 0.0
    intent: str = ""
    problems_count: int = 0
    lessons_count: int = 0
    label_reason: str = ""


class TopicEntry(BaseModel):
    """A topic cluster in the report."""
    topic_id: str
    topic_name: str
    topic_summary: str = ""
    session_count: int = 0
    session_ids: list[str] = Field(default_factory=list)
    rule_count: int = 0
    skill_title: str = ""
    description: str = ""
    rules: list[SkillRule] = Field(default_factory=list)
    output_path: str = ""


class StepTiming(BaseModel):
    """Timing for a pipeline step."""
    name: str
    start: str = ""
    end: str = ""
    duration_seconds: float = 0.0


class LLMUsage(BaseModel):
    """LLM API usage stats."""
    label: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class DistillReport(BaseModel):
    """Full report of a distillation run."""
    run_id: str = ""
    project: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_duration_seconds: float = 0.0

    sessions_total: int = 0
    sessions_passed_filter: int = 0
    sessions: list[SessionEntry] = Field(default_factory=list)

    topics_found: int = 0
    unclustered_count: int = 0
    topics: list[TopicEntry] = Field(default_factory=list)

    total_rules: int = 0

    steps: list[StepTiming] = Field(default_factory=list)
    llm_usage: list[LLMUsage] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)
    output_dir: str = ""


class RunState(BaseModel):
    """Persistent state for incremental processing and scheduling."""
    last_run: str = ""
    last_session_id: str = ""
    processed_sessions: list[str] = Field(default_factory=list)
    cost_accumulated: float = 0.0
    stats: dict[str, int] = Field(default_factory=dict)


class ShapingResult(BaseModel):
    """Result of the output shaping step."""
    written_paths: list[Path] = []
    index_path: Path | None = None
    report_path: Path | None = None

    class Config:
        arbitrary_types_allowed = True
