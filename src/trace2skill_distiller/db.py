"""OpenCode session data access — SQLite metadata + export command."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from .config import DistillConfig
from .models import Session


def get_db_path(config: Optional[DistillConfig] = None) -> Path:
    if config:
        p = Path(config.opencode.db_path).expanduser()
    else:
        p = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    return p


def list_sessions(
    config: Optional[DistillConfig] = None,
    project: Optional[str] = None,
    since: Optional[int] = None,
) -> list[dict]:
    """List sessions from SQLite, optionally filtered."""
    db_path = get_db_path(config)
    if not db_path.exists():
        raise FileNotFoundError(f"OpenCode database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT s.id, s.project_id, s.slug, s.directory, s.title,
               s.time_created, s.time_updated,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) AS msg_count
        FROM session s
        WHERE 1=1
    """
    params: list = []

    if project:
        query += " AND s.directory LIKE ?"
        params.append(f"%{project}%")

    if since:
        query += " AND s.time_updated > ?"
        params.append(since)

    query += " ORDER BY s.time_updated DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def count_tools(session_id: str, config: Optional[DistillConfig] = None) -> int:
    """Count tool-call parts for a session."""
    db_path = get_db_path(config)
    conn = sqlite3.connect(str(db_path))
    result = conn.execute(
        """
        SELECT COUNT(*) FROM part p
        WHERE p.session_id = ?
          AND json_extract(p.data, '$.type') = 'tool'
        """,
        (session_id,),
    ).fetchone()
    conn.close()
    return result[0] if result else 0


def _find_opencode() -> str:
    """Find the opencode binary."""
    # Check common locations
    candidates = [
        Path.home() / "AppData" / "Roaming" / "npm" / "opencode.cmd",
        Path.home() / "AppData" / "Roaming" / "npm" / "opencode",
        Path("/usr/local/bin/opencode"),
        Path.home() / ".local" / "bin" / "opencode",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "opencode"  # fallback to PATH


def export_session(session_id: str) -> Optional[Session]:
    """Export a session via `opencode export` command and parse it."""
    opencode_bin = _find_opencode()
    try:
        result = subprocess.run(
            [opencode_bin, "export", session_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"opencode command not found at {opencode_bin}. "
            "Is OpenCode CLI installed?"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Export timed out for session {session_id}")

    if not result.stdout.strip():
        raise RuntimeError(
            f"Empty export output for session {session_id}. "
            f"stderr: {result.stderr[:200]}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse export JSON: {e}")

    return Session.model_validate(data)
