"""Standalone entry point for PyInstaller packaging.

This wrapper avoids relative-import issues when main.py is executed
directly by the PyInstaller bootloader.
"""

import sys
from pathlib import Path

# Ensure the src/ package root is on sys.path so that
# trace2skill_distiller can be imported as a top-level package.
_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from trace2skill_distiller.cli.main import cli  # noqa: E402

if __name__ == "__main__":
    cli()
