"""Tests for the native PySide6 GUI dispatcher and window construction.

These run headless by forcing the ``offscreen`` Qt platform plugin. They do not
drive the full pipeline (that is covered by the pipeline + services tests); they
only assert the Qt layer wires up correctly and that argv dispatch routes to the
CLI when arguments are present.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Force headless rendering before importing Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from trace2skill_distiller.gui import qt_app  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_with_argv_delegates_to_cli():
    """When argv is non-empty, main() must hand off to the Click CLI, not GUI."""
    with patch("trace2skill_distiller.cli.main.cli") as mock_cli:
        rc = qt_app.main(["--help"])
    mock_cli.assert_called_once()
    # Click's standalone_mode=False is used; main returns 0 on clean exit.
    assert rc == 0


def test_main_empty_argv_uses_window(monkeypatch):
    """With no argv, main() should build a QApplication and open the window."""
    launched = {"window": False}

    class _FakeApp:
        def __init__(self, *_a, **_k):
            pass

        def setApplicationName(self, _n):
            pass

        def exec(self):
            return 0

    def _fake_run_window():
        launched["window"] = True
        return 0

    monkeypatch.setattr(qt_app, "QApplication", _FakeApp)
    monkeypatch.setattr(qt_app, "_run_window", _fake_run_window)
    rc = qt_app.main([])
    assert rc == 0
    assert launched["window"] is True


def test_main_window_constructs(qapp):
    """MainWindow builds without error and exposes the two expected tabs."""
    win = qt_app.MainWindow()
    assert win.tabs.count() == 2
    assert win.tabs.tabText(0) == "记忆整理"
    assert win.tabs.tabText(1) == "设置"
    assert isinstance(win.extract, qt_app.ExtractPanel)
    assert isinstance(win.settings, qt_app.SettingsPanel)


def test_decision_copy_has_three_actions():
    assert set(qt_app._DECISION_COPY.keys()) == {"confirm", "review", "archive"}
    for key, copy in qt_app._DECISION_COPY.items():
        assert "label" in copy and "effect" in copy and "done" in copy
        assert "{target}" in copy["effect"]
