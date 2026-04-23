"""Configuration management for trace2skill-distiller."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    provider: str = "openai"
    model: str = "openai/gpt-oss-120b"
    max_tokens: int = 4096
    api_key: str = ""
    base_url: str = ""


class OpenCodeConfig(BaseModel):
    db_path: str = "~/.local/share/opencode/opencode.db"
    export_command: str = "opencode export"


class DistillFilter(BaseModel):
    min_messages: int = 5
    min_tools: int = 3
    projects: list[str] = Field(default_factory=list)


class SchedulerConfig(BaseModel):
    enabled: bool = False
    cron: str = "0 3 * * *"
    timezone: str = "Asia/Shanghai"
    strategy: str = "incremental"
    min_new_sessions: int = 3
    min_new_messages: int = 50
    max_idle_days: int = 7
    max_sessions_per_run: int = 20
    max_cost_per_run: float = 1.0
    max_runtime_minutes: int = 30


class DistillConfig(BaseModel):
    fast_model: ModelConfig = Field(default_factory=ModelConfig)
    strong_model: ModelConfig = Field(default_factory=ModelConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    filter: DistillFilter = Field(default_factory=DistillFilter)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    skill_output_dir: str = "~/.trace2skill/skills"
    max_rules_per_skill: int = 15

    @staticmethod
    def default_config_path() -> Path:
        return Path.home() / ".trace2skill" / "config.yaml"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "DistillConfig":
        path = path or cls.default_config_path()
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}

        # Environment variable overrides
        env_key = os.getenv("TRACE2SKILL_API_KEY")
        env_url = os.getenv("TRACE2SKILL_BASE_URL")
        env_fast = os.getenv("TRACE2SKILL_FAST_MODEL")
        env_strong = os.getenv("TRACE2SKILL_STRONG_MODEL")

        models = raw.get("models", {})
        fast = models.get("fast", {})
        strong = models.get("strong", {})

        fast_model = ModelConfig(
            api_key=env_key or fast.get("api_key", ""),
            base_url=env_url or fast.get("base_url", ""),
            model=env_fast or fast.get("model", "openai/gpt-oss-120b"),
            max_tokens=fast.get("max_tokens", 4096),
        )
        strong_model = ModelConfig(
            api_key=env_key or strong.get("api_key", fast_model.api_key),
            base_url=env_url or strong.get("base_url", fast_model.base_url),
            model=env_strong or strong.get("model", "openai/gpt-oss-120b"),
            max_tokens=strong.get("max_tokens", 8192),
        )

        oc = raw.get("opencode", {})
        fl = raw.get("filter", {})
        sched = raw.get("scheduler", {})

        return cls(
            fast_model=fast_model,
            strong_model=strong_model,
            opencode=OpenCodeConfig(**oc),
            filter=DistillFilter(**fl),
            scheduler=SchedulerConfig(**sched),
            skill_output_dir=raw.get("skill_output_dir", "~/.trace2skill/skills"),
            max_rules_per_skill=raw.get("max_rules_per_skill", 15),
        )


def init_default_config(
    api_key: str,
    base_url: str,
    fast_model: str,
    strong_model: str,
) -> Path:
    """Create default config.yaml with provided credentials."""
    config_dir = Path.home() / ".trace2skill"
    config_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "models": {
            "fast": {"model": fast_model, "max_tokens": 4096},
            "strong": {"model": strong_model, "max_tokens": 8192},
        },
        "opencode": {
            "db_path": "~/.local/share/opencode/opencode.db",
        },
        "filter": {
            "min_messages": 5,
            "min_tools": 3,
        },
        "scheduler": {
            "enabled": False,
            "cron": "0 3 * * *",
        },
    }
    config_path = config_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    # Write API key to .env file
    env_path = config_dir / ".env"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"TRACE2SKILL_API_KEY={api_key}\n")
        f.write(f"TRACE2SKILL_BASE_URL={base_url}\n")

    return config_path
