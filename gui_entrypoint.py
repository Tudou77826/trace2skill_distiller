"""PyInstaller entry point for the Trace2Skill GUI.

Single executable, dual purpose:
  - launched with no CLI arguments  -> native PySide6 desktop window
  - launched with CLI arguments     -> delegates to the ``trace2skill`` CLI
"""

from __future__ import annotations

import sys


def main() -> int:
    # Ensure src/ is importable when running from a source checkout.
    sys.path.insert(0, "src")
    from trace2skill_distiller.gui.qt_app import main as qt_main

    return qt_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
