"""Configuration management for trace2skill-distiller."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for a single LLM endpoint."""
    model: str = "openai/gpt-oss-120b"
    max_tokens: int = 4096
    api_key: str = ""
    base_url: str = ""
    verify_ssl: bool = False
    proxy: str = ""  # empty = no proxy
    proxy_bypass: str = ""  # comma-separated regex patterns for hosts that bypass proxy
    timeout: float = 120.0
    connect_timeout: float = 10.0
    extra_headers: dict[str, str] = Field(default_factory=dict)
    user_agent: str = "curl/8.0"

    @classmethod
    def from_yaml(cls, data: dict, defaults: "LLMConfig | None" = None) -> "LLMConfig":
        """Build LLMConfig from a YAML dict, falling back to defaults."""
        d = defaults
        return cls(
            api_key=data.get("api_key", d.api_key if d else ""),
            base_url=data.get("base_url", d.base_url if d else ""),
            model=data.get("model", d.model if d else "openai/gpt-oss-120b"),
            max_tokens=data.get("max_tokens", d.max_tokens if d else 4096),
            verify_ssl=data.get("verify_ssl", d.verify_ssl if d else False),
            proxy=data.get("proxy", d.proxy if d else ""),
            proxy_bypass=data.get("proxy_bypass", d.proxy_bypass if d else ""),
            timeout=data.get("timeout", d.timeout if d else 120.0),
            connect_timeout=data.get("connect_timeout", d.connect_timeout if d else 10.0),
            extra_headers=data.get("extra_headers", d.extra_headers if d else {}),
            user_agent=data.get("user_agent", d.user_agent if d else "curl/8.0"),
        )


# Backward-compatible alias
ModelConfig = LLMConfig


class OpenCodeConfig(BaseModel):
    db_path: str = "~/.local/share/opencode/opencode.db"
    export_command: str = "opencode export"


class ChrysConfig(BaseModel):
    sessions_dir: str = ""  # empty = auto-detect per platform


class SourceConfig(BaseModel):
    """Data source configuration — selects which Coding Agent to mine from."""
    type: str = "opencode"  # opencode | chrys
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    chrys: ChrysConfig = Field(default_factory=ChrysConfig)


class DistillFilter(BaseModel):
    min_messages: int = 5
    min_tools: int = 3
    projects: list[str] = Field(default_factory=list)


class AnalysisConfig(BaseModel):
    """Configuration for the analysis layer."""
    clustering_min_size: int = 1
    clustering_max_topics: int = 8
    protected_topics: list[str] = Field(default_factory=list)


class OutputConfig(BaseModel):
    """Configuration for the output layer."""
    skill_output_dir: str = "~/.trace2skill/skills"
    max_rules_per_skill: int = 15
    format: str = "skill_md"  # skill_md | knowledge_md


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
    fast_model: LLMConfig = Field(default_factory=LLMConfig)
    strong_model: LLMConfig = Field(default_factory=LLMConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    filter: DistillFilter = Field(default_factory=DistillFilter)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

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
        env_verify_ssl = os.getenv("TRACE2SKILL_VERIFY_SSL")
        env_proxy = os.getenv("TRACE2SKILL_PROXY")
        env_proxy_bypass = os.getenv("TRACE2SKILL_PROXY_BYPASS")
        env_timeout = os.getenv("TRACE2SKILL_TIMEOUT")
        env_connect_timeout = os.getenv("TRACE2SKILL_CONNECT_TIMEOUT")
        env_source_type = os.getenv("TRACE2SKILL_SOURCE")

        models = raw.get("models", {})
        fast_data = models.get("fast", {})
        strong_data = models.get("strong", {})

        # Shared env overrides — apply to both models
        shared_overrides: dict = {}
        if env_key:
            shared_overrides["api_key"] = env_key
        if env_url:
            shared_overrides["base_url"] = env_url
        if env_proxy:
            shared_overrides["proxy"] = env_proxy
        if env_proxy_bypass:
            shared_overrides["proxy_bypass"] = env_proxy_bypass
        if env_timeout:
            shared_overrides["timeout"] = float(env_timeout)
        if env_connect_timeout:
            shared_overrides["connect_timeout"] = float(env_connect_timeout)
        if env_verify_ssl is not None:
            shared_overrides["verify_ssl"] = env_verify_ssl.lower() == "true"

        # Build fast model
        fast_model = LLMConfig.from_yaml(fast_data)
        fast_overrides = {**shared_overrides}
        if env_fast:
            fast_overrides["model"] = env_fast
        if fast_overrides:
            fast_model = fast_model.model_copy(update=fast_overrides)

        # Build strong model with fast as fallback defaults
        strong_model = LLMConfig.from_yaml(strong_data, defaults=fast_model)
        strong_overrides = {**shared_overrides}
        if env_strong:
            strong_overrides["model"] = env_strong
        if strong_overrides:
            strong_model = strong_model.model_copy(update=strong_overrides)

        fl = raw.get("filter", {})
        sched = raw.get("scheduler", {})

        # Source config — backward compatible with old `opencode:` top-level key
        src_raw = raw.get("source", {})
        if not src_raw and raw.get("opencode"):
            # Migrate old format: opencode.db_path → source.opencode.db_path
            src_raw = {"type": "opencode", "opencode": raw["opencode"]}
        src_type = env_source_type or src_raw.get("type", "opencode")
        src_opencode = OpenCodeConfig(**src_raw.get("opencode", {}))
        src_chrys = ChrysConfig(**src_raw.get("chrys", {}))

        return cls(
            fast_model=fast_model,
            strong_model=strong_model,
            source=SourceConfig(
                type=src_type,
                opencode=src_opencode,
                chrys=src_chrys,
            ),
            filter=DistillFilter(**fl),
            scheduler=SchedulerConfig(**sched),
            analysis=AnalysisConfig(
                clustering_min_size=raw.get("clustering_min_size", 1),
                clustering_max_topics=raw.get("clustering_max_topics", 8),
                protected_topics=raw.get("protected_topics", []),
            ),
            output=OutputConfig(
                skill_output_dir=raw.get("skill_output_dir", "~/.trace2skill/skills"),
                max_rules_per_skill=raw.get("max_rules_per_skill", 15),
            ),
        )


def init_default_config(
    api_key: str,
    base_url: str,
    fast_model: str,
    strong_model: str,
    proxy: str = "",
    proxy_bypass: str = "",
    verify_ssl: bool = False,
    timeout: float = 120.0,
    connect_timeout: float = 10.0,
    source_type: str = "opencode",
) -> Path:
    """Create default config.yaml with provided credentials."""
    config_dir = Path.home() / ".trace2skill"
    config_dir.mkdir(parents=True, exist_ok=True)

    fast_cfg: dict = {"model": fast_model, "max_tokens": 4096}
    strong_cfg: dict = {"model": strong_model, "max_tokens": 8192}
    if verify_ssl:
        fast_cfg["verify_ssl"] = True
        strong_cfg["verify_ssl"] = True
    if proxy:
        fast_cfg["proxy"] = proxy
        strong_cfg["proxy"] = proxy
    if proxy_bypass:
        fast_cfg["proxy_bypass"] = proxy_bypass
        strong_cfg["proxy_bypass"] = proxy_bypass
    if timeout != 120.0:
        fast_cfg["timeout"] = timeout
        strong_cfg["timeout"] = timeout
    if connect_timeout != 10.0:
        fast_cfg["connect_timeout"] = connect_timeout
        strong_cfg["connect_timeout"] = connect_timeout

    config: dict = {
        "models": {
            "fast": fast_cfg,
            "strong": strong_cfg,
        },
        "source": {
            "type": source_type,
            "opencode": {
                "db_path": "~/.local/share/opencode/opencode.db",
            },
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


# Mapping: dotted key -> (YAML path segments, type converter)
_CONFIG_KEY_MAP: dict[str, tuple[list[str], type]] = {
    "fast.model": (["models", "fast", "model"], str),
    "fast.max_tokens": (["models", "fast", "max_tokens"], int),
    "fast.api_key": (["models", "fast", "api_key"], str),
    "fast.base_url": (["models", "fast", "base_url"], str),
    "fast.verify_ssl": (["models", "fast", "verify_ssl"], bool),
    "fast.proxy": (["models", "fast", "proxy"], str),
    "fast.proxy_bypass": (["models", "fast", "proxy_bypass"], str),
    "fast.timeout": (["models", "fast", "timeout"], float),
    "fast.connect_timeout": (["models", "fast", "connect_timeout"], float),
    "fast.user_agent": (["models", "fast", "user_agent"], str),
    "strong.model": (["models", "strong", "model"], str),
    "strong.max_tokens": (["models", "strong", "max_tokens"], int),
    "strong.api_key": (["models", "strong", "api_key"], str),
    "strong.base_url": (["models", "strong", "base_url"], str),
    "strong.verify_ssl": (["models", "strong", "verify_ssl"], bool),
    "strong.proxy": (["models", "strong", "proxy"], str),
    "strong.proxy_bypass": (["models", "strong", "proxy_bypass"], str),
    "strong.timeout": (["models", "strong", "timeout"], float),
    "strong.connect_timeout": (["models", "strong", "connect_timeout"], float),
    "strong.user_agent": (["models", "strong", "user_agent"], str),
    "source.type": (["source", "type"], str),
    "source.opencode.db_path": (["source", "opencode", "db_path"], str),
    "source.opencode.export_command": (["source", "opencode", "export_command"], str),
    "source.chrys.sessions_dir": (["source", "chrys", "sessions_dir"], str),
}


def set_config_value(key: str, value: str) -> None:
    """Set a single config value by dotted key (e.g. 'fast.proxy').

    Reads config.yaml, sets the value at the mapped path, and writes back.
    Supports type conversion: bool ("true"/"false"), int, float, str.
    """
    if key not in _CONFIG_KEY_MAP:
        if key.endswith(".extra_headers"):
            raise ValueError(
                f"Cannot set '{key}' via CLI — extra_headers is a dict. "
                "Edit config.yaml directly: trace2skill config edit"
            )
        valid = ", ".join(sorted(_CONFIG_KEY_MAP.keys()))
        raise ValueError(f"Unknown config key: {key}. Valid keys: {valid}")

    path_segments, converter = _CONFIG_KEY_MAP[key]

    # Type conversion
    if converter is bool:
        converted: bool | int | float | str = value.lower() in ("true", "1", "yes")
    else:
        converted = converter(value)

    config_path = DistillConfig.default_config_path()
    if not config_path.exists():
        raise ValueError(
            f"Config file not found at {config_path}. Run 'trace2skill init' first."
        )
    with open(config_path, encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    # Walk to the parent dict, creating intermediates as needed
    node = raw
    for segment in path_segments[:-1]:
        node = node.setdefault(segment, {})
    node[path_segments[-1]] = converted

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
