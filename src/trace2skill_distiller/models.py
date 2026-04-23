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
    dimension: str = ""
    project: str = ""
    rules: list[SkillRule] = Field(default_factory=list)
    deprecated_rules: list[dict[str, str]] = Field(default_factory=list)


class RunState(BaseModel):
    """Persistent state for incremental processing and scheduling."""

    last_run: str = ""
    last_session_id: str = ""
    processed_sessions: list[str] = Field(default_factory=list)
    cost_accumulated: float = 0.0
    stats: dict[str, int] = Field(default_factory=dict)
