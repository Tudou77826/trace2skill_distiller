"""Data models for OpenCode session export format."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Part types ──


class PartText(BaseModel):
    type: str = "text"
    text: str
    time: dict[str, int] = Field(default_factory=dict)
    id: str = ""
    sessionID: str = ""
    messageID: str = ""


class PartReasoning(BaseModel):
    type: str = "reasoning"
    text: str
    time: dict[str, int] = Field(default_factory=dict)
    id: str = ""
    sessionID: str = ""
    messageID: str = ""


class ToolState(BaseModel):
    status: str = "completed"
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    time: dict[str, int] = Field(default_factory=dict)


class PartTool(BaseModel):
    type: str = "tool"
    callID: str = ""
    tool: str = ""
    state: ToolState = Field(default_factory=ToolState)
    id: str = ""
    sessionID: str = ""
    messageID: str = ""


class PartPatch(BaseModel):
    type: str = "patch"
    hash: str = ""
    files: list[str] = Field(default_factory=list)


class PartSubtask(BaseModel):
    type: str = "subtask"
    prompt: str = ""
    description: str = ""
    agent: str = ""


class PartStepStart(BaseModel):
    type: str = "step-start"


class PartStepFinish(BaseModel):
    type: str = "step-finish"
    reason: str = ""
    cost: float = 0
    tokens: dict[str, Any] = Field(default_factory=dict)


# ── Message ──


class TokenInfo(BaseModel):
    total: int = 0
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache: dict[str, int] = Field(default_factory=dict)


class MessageInfo(BaseModel):
    role: str
    time: dict[str, int] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    agent: str = ""
    modelID: str = ""
    providerID: str = ""
    mode: str = ""
    cost: float = 0
    tokens: TokenInfo = Field(default_factory=TokenInfo)
    finish: str = ""
    parentID: str = ""
    path: dict[str, str] = Field(default_factory=dict)
    id: str = ""
    sessionID: str = ""
    error: Optional[dict[str, Any]] = None


class Message(BaseModel):
    """A single message in a session (user or assistant)."""

    info: MessageInfo
    parts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def role(self) -> str:
        return self.info.role

    @property
    def text_parts(self) -> list[str]:
        return [p["text"] for p in self.parts if p.get("type") == "text"]

    @property
    def reasoning_parts(self) -> list[str]:
        return [p["text"] for p in self.parts if p.get("type") == "reasoning"]

    @property
    def tool_parts(self) -> list[dict]:
        return [p for p in self.parts if p.get("type") == "tool"]

    @property
    def patch_parts(self) -> list[dict]:
        return [p for p in self.parts if p.get("type") == "patch"]

    @property
    def subtask_parts(self) -> list[dict]:
        return [p for p in self.parts if p.get("type") == "subtask"]


# ── Session ──


class SessionSummary(BaseModel):
    additions: int = 0
    deletions: int = 0
    files: int = 0


class SessionInfo(BaseModel):
    id: str
    slug: str = ""
    projectID: str = ""
    directory: str = ""
    title: str = ""
    version: str = ""
    summary: SessionSummary = Field(default_factory=SessionSummary)
    time: dict[str, int] = Field(default_factory=dict)


class Session(BaseModel):
    """Complete session export from `opencode export`."""

    info: SessionInfo
    messages: list[Message] = Field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.info.id

    @property
    def project_name(self) -> str:
        """Extract project name from directory path."""
        d = self.info.directory.replace("\\", "/")
        return d.rstrip("/").split("/")[-1] if d else "unknown"

    @property
    def user_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "user"]

    @property
    def assistant_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "assistant"]

    @property
    def tool_count(self) -> int:
        return sum(len(m.tool_parts) for m in self.messages)

    @property
    def has_patches(self) -> bool:
        return any(len(m.patch_parts) > 0 for m in self.messages)

    @property
    def has_errors(self) -> bool:
        return any(m.info.error is not None for m in self.messages)

    @property
    def last_assistant_finish(self) -> str:
        assistants = self.assistant_messages
        if assistants:
            return assistants[-1].info.finish
        return ""

    @property
    def total_tokens(self) -> int:
        return sum(m.info.tokens.total for m in self.assistant_messages)


# ── Preprocessing outputs ──


class IntentBlock(BaseModel):
    """A coherent segment of a session, identified by quick-LLM."""

    block_id: int
    message_range: tuple[int, int]  # start/end index in messages list
    intent: str
    user_inputs: list[str] = Field(default_factory=list)


class TrajectorySummary(BaseModel):
    """Structured output of Level 2 preprocessing."""

    session_id: str
    session_type: str = ""  # feature_development | debugging | exploration | ...
    project: str = ""

    intent: str = ""
    what_happened: list[PhaseSummary] = Field(default_factory=list)
    problems_encountered: list[ProblemRecord] = Field(default_factory=list)
    key_decisions: list[DecisionRecord] = Field(default_factory=list)
    lessons_learned: list[str] = Field(default_factory=list)

    label: str = ""  # success | partial | failure
    label_score: float = 0.0


class PhaseSummary(BaseModel):
    phase: str = ""
    summary: str = ""


class ProblemRecord(BaseModel):
    problem: str = ""
    how_resolved: str = ""
    lessons: str = ""


class DecisionRecord(BaseModel):
    decision: str = ""
    rationale: str = ""
    outcome: str = ""


# ── Distillation outputs ──


class SkillRule(BaseModel):
    id: str = ""
    type: str = ""  # ALWAYS | WHEN_THEN | NEVER | AVOID
    condition: str = ""
    action: str = ""
    evidence_from_success: list[str] = Field(default_factory=list)
    evidence_from_failure: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    scope: str = "general"  # general | project-specific | language-specific


class SkillPatch(BaseModel):
    """Deprecated: kept for backward compatibility."""
    dimension: str = ""
    project: str = ""
    rules: list[SkillRule] = Field(default_factory=list)
    deprecated_rules: list[dict[str, str]] = Field(default_factory=list)


# ── Topic-based distillation ──


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
    skill_type: str = "checklist"  # procedure | knowledge | checklist | troubleshooting | reference
    description: str       # English, specific, with trigger words (for AI auto-discovery)
    summary: str           # 一段概述
    rules: list[SkillRule] = Field(default_factory=list)
    body: str = ""         # LLM-generated Markdown body (format determined by skill_type)
    source_sessions: list[str] = Field(default_factory=list)


class ClusteringResult(BaseModel):
    """Output of the topic clustering step."""
    clusters: list[TopicCluster] = Field(default_factory=list)
    unclustered: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    """Persistent state for incremental processing and scheduling."""

    last_run: str = ""
    last_session_id: str = ""
    processed_sessions: list[str] = Field(default_factory=list)
    cost_accumulated: float = 0.0
    stats: dict[str, int] = Field(default_factory=dict)


# ── Report ──


class SessionEntry(BaseModel):
    """A single session in the report."""
    session_id: str
    title: str = ""
    project: str = ""
    msg_count: int = 0
    tool_count: int = 0
    label: str = ""           # success | partial | failure | skipped
    label_score: float = 0.0
    intent: str = ""
    problems_count: int = 0
    lessons_count: int = 0
    label_reason: str = ""    # brief reason for non-success label


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
    label: str          # "fast" or "strong"
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
