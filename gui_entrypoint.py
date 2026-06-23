"""Standalone GUI entry point for PyInstaller packaging."""

from __future__ import annotations

import socket
import sys
from pathlib import Path


_src = str(Path(__file__).resolve().parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from trace2skill_distiller.gui.server import run_gui  # noqa: E402


def _available_port(start: int = 8765, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No available local port found from {start} to {start + attempts - 1}.")


if __name__ == "__main__":
    port = _available_port()
    run_gui(host="127.0.0.1", port=port, open_browser=True)
