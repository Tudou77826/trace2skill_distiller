"""Tests for core.config — LLMConfig.from_yaml, DistillConfig.load, set_config_value, init_default_config."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml
import pytest
from unittest.mock import patch

from trace2skill_distiller.core.config import (
    DistillConfig,
    LLMConfig,
    set_config_value,
    init_default_config,
)


# ── LLMConfig.from_yaml ──

class TestFromYaml:
    def test_full_data(self):
        data = {
            "model": "m1", "max_tokens": 8192, "api_key": "sk-x",
            "base_url": "http://api", "verify_ssl": True,
            "proxy": "socks5://p:1", "proxy_bypass": "localhost",
            "timeout": 60.0, "connect_timeout": 5.0,
            "extra_headers": {"X": "1"}, "user_agent": "ua/1",
        }
        cfg = LLMConfig.from_yaml(data)
        assert cfg.model == "m1"
        assert cfg.proxy_bypass == "localhost"
        assert cfg.timeout == 60.0
        assert cfg.connect_timeout == 5.0
        assert cfg.extra_headers == {"X": "1"}
        assert cfg.user_agent == "ua/1"

    def test_empty_data_uses_class_defaults(self):
        cfg = LLMConfig.from_yaml({})
        assert cfg.api_key == ""
        assert cfg.timeout == 120.0
        assert cfg.proxy == ""
        assert cfg.proxy_bypass == ""

    def test_fallback_to_defaults_param(self):
        parent = LLMConfig(api_key="sk-parent", proxy="http://p", timeout=99.0)
        cfg = LLMConfig.from_yaml({"model": "child"}, defaults=parent)
        assert cfg.model == "child"
        assert cfg.api_key == "sk-parent"
        assert cfg.proxy == "http://p"
        assert cfg.timeout == 99.0

    def test_partial_data_overrides_defaults(self):
        parent = LLMConfig(timeout=99.0, proxy="http://p")
        cfg = LLMConfig.from_yaml({"timeout": 50.0}, defaults=parent)
        assert cfg.timeout == 50.0
        assert cfg.proxy == "http://p"


# ── DistillConfig.load ──

class TestDistillConfigLoad:
    def _write_yaml(self, data: dict) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
        yaml.dump(data, f)
        f.close()
        return f.name

    def test_loads_all_llm_fields(self):
        path = self._write_yaml({
            "models": {
                "fast": {
                    "model": "fast-m", "timeout": 90.0,
                    "proxy": "http://p:1", "proxy_bypass": "localhost",
                    "extra_headers": {"X": "1"}, "user_agent": "ua/1",
                },
                "strong": {"model": "strong-m"},
            },
            "opencode": {},
            "filter": {},
        })
        cfg = DistillConfig.load(Path(path))
        os.unlink(path)

        assert cfg.fast_model.timeout == 90.0
        assert cfg.fast_model.proxy_bypass == "localhost"
        assert cfg.fast_model.extra_headers == {"X": "1"}
        assert cfg.fast_model.user_agent == "ua/1"
        # strong inherits from fast
        assert cfg.strong_model.proxy == "http://p:1"
        assert cfg.strong_model.api_key == cfg.fast_model.api_key

    def test_env_overrides(self):
        path = self._write_yaml({
            "models": {"fast": {"api_key": "yaml-key"}, "strong": {}},
            "opencode": {},
            "filter": {},
        })
        os.environ["TRACE2SKILL_API_KEY"] = "env-key"
        try:
            cfg = DistillConfig.load(Path(path))
            assert cfg.fast_model.api_key == "env-key"
            assert cfg.strong_model.api_key == "env-key"
        finally:
            del os.environ["TRACE2SKILL_API_KEY"]
            os.unlink(path)

    def test_no_duplicate_top_level_fields(self):
        fields = list(DistillConfig.model_fields.keys())
        for dup in ("skill_output_dir", "max_rules_per_skill", "clustering_min_size",
                     "clustering_max_topics", "protected_topics", "mining"):
            assert dup not in fields, f"{dup} should not be on DistillConfig"

    def test_sub_model_fields_populated(self):
        path = self._write_yaml({
            "models": {"fast": {}, "strong": {}},
            "clustering_min_size": 3,
            "max_rules_per_skill": 20,
            "skill_output_dir": "/tmp/skills",
        })
        cfg = DistillConfig.load(Path(path))
        os.unlink(path)
        assert cfg.analysis.clustering_min_size == 3
        assert cfg.output.max_rules_per_skill == 20
        assert cfg.output.skill_output_dir == "/tmp/skills"

    def test_missing_file_uses_defaults(self):
        cfg = DistillConfig.load(Path("/nonexistent/config.yaml"))
        assert cfg.fast_model.model == "openai/gpt-oss-120b"


# ── set_config_value ──

class TestSetConfigValue:
    def test_set_writes_to_yaml(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
        yaml.dump({"models": {"fast": {"model": "old"}}}, f)
        f.close()
        # Monkey-patch default path
        orig = DistillConfig.default_config_path
        DistillConfig.default_config_path = staticmethod(lambda: Path(f.name))
        try:
            set_config_value("fast.model", "new-model")
            with open(f.name) as fh:
                data = yaml.safe_load(fh)
            assert data["models"]["fast"]["model"] == "new-model"
        finally:
            DistillConfig.default_config_path = orig
            os.unlink(f.name)

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="Unknown config key"):
            set_config_value("bogus.field", "x")

    def test_rejects_extra_headers_with_helpful_message(self):
        with pytest.raises(ValueError, match="extra_headers is a dict"):
            set_config_value("fast.extra_headers", "{}")

    def test_rejects_missing_config_file(self):
        orig = DistillConfig.default_config_path
        DistillConfig.default_config_path = staticmethod(lambda: Path("/nonexistent.yaml"))
        try:
            with pytest.raises(ValueError, match="not found"):
                set_config_value("fast.proxy", "http://x")
        finally:
            DistillConfig.default_config_path = orig


# ── init_default_config ──

class TestInitDefaultConfig:
    def test_writes_all_fields(self, tmp_path):
        with patch("trace2skill_distiller.core.config.Path.home", return_value=tmp_path):
            result = init_default_config(
                "sk-test", "http://api", "fast-m", "strong-m",
                proxy="http://p:1", proxy_bypass="localhost",
                verify_ssl=True, timeout=60.0, connect_timeout=5.0,
            )
            config_path = tmp_path / ".trace2skill" / "config.yaml"
            assert config_path.exists()
            with open(config_path) as f:
                data = yaml.safe_load(f)
            fast = data["models"]["fast"]
            strong = data["models"]["strong"]
            assert fast["proxy"] == "http://p:1"
            assert fast["proxy_bypass"] == "localhost"
            assert fast["verify_ssl"] is True
            assert fast["timeout"] == 60.0
            assert fast["connect_timeout"] == 5.0
            assert strong["proxy"] == "http://p:1"
            assert strong["verify_ssl"] is True
            assert strong["timeout"] == 60.0
            assert strong["connect_timeout"] == 5.0
            env_path = tmp_path / ".trace2skill" / ".env"
            assert env_path.exists()
            assert "TRACE2SKILL_API_KEY=sk-test" in env_path.read_text()
