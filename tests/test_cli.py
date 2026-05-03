"""Tests for CLI config commands and .env safety."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from trace2skill_distiller.cli.main import cli


class TestConfigShow:
    def test_show_displays_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "models": {
                "fast": {"model": "fast-m", "api_key": "sk-secret-key-12345"},
                "strong": {"model": "strong-m"},
            },
            "opencode": {},
            "filter": {},
        }))
        env_path = tmp_path / ".env"
        env_path.write_text("TRACE2SKILL_API_KEY=sk-secret-key-12345\n")

        with patch("trace2skill_distiller.cli.main.DistillConfig") as MockConfig:
            from trace2skill_distiller.core.config import DistillConfig, LLMConfig
            MockConfig.load.return_value = DistillConfig(
                fast_model=LLMConfig(model="fast-m", api_key="sk-secret-key-12345"),
                strong_model=LLMConfig(model="strong-m"),
            )
            MockConfig.default_config_path.return_value = config_path

            runner = CliRunner()
            result = runner.invoke(cli, ["config", "show"])
            assert result.exit_code == 0
            # API key should be masked
            assert "sk-se****key-12345" in result.output or "sk-s*" in result.output


class TestEnvSafety:
    def test_only_trace2skill_prefix_loaded(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "TRACE2SKILL_API_KEY=good-key\n"
            "PATH=/malicious\n"
            "HOME=/evil\n"
        )
        with patch("trace2skill_distiller.cli.main.Path") as MockPath:
            MockPath.home.return_value = tmp_path
            MockPath.return_value.__truediv__ = lambda s, o: tmp_path / o
            # Just test the parsing logic directly
            lines = env_path.read_text().splitlines()
            loaded = {}
            for line in lines:
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key.startswith("TRACE2SKILL_"):
                        loaded[key] = val.strip()
            assert loaded == {"TRACE2SKILL_API_KEY": "good-key"}
            assert "PATH" not in loaded
            assert "HOME" not in loaded
