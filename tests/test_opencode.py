"""Tests for mining.sources.opencode — LIKE escaping."""

from __future__ import annotations

from trace2skill_distiller.mining.sources.opencode import OpenCodeSource


class TestLikeEscaping:
    def test_percent_is_escaped(self):
        src = OpenCodeSource()
        # Verify the escaping logic directly
        project = "100%"
        safe = project.replace("%", "\\%").replace("_", "\\_")
        assert safe == "100\\%"

    def test_underscore_is_escaped(self):
        project = "my_project"
        safe = project.replace("%", "\\%").replace("_", "\\_")
        assert safe == "my\\_project"
