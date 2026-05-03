"""OpenCode data source — SQLite metadata + CLI export."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from .base import SessionSource
from ..types import Session, SessionMeta


class OpenCodeSource:
    """OpenCode data source: reads from SQLite and exports via CLI."""

    def __init__(self, db_path: str = "~/.local/share/opencode/opencode.db", export_command: str = "opencode export"):
        self._db_path = Path(db_path).expanduser()
        self._export_command = export_command

    def _get_db(self) -> Path:
        return self._db_path

    def list_sessions(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        """List sessions from SQLite, optionally filtered."""
        db_path = self._get_db()
        if not db_path.exists():
            raise FileNotFoundError(f"OpenCode database not found: {db_path}")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        try:
            query = """
                SELECT s.id, s.project_id, s.slug, s.directory, s.title,
                       s.time_created, s.time_updated,
                       (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) AS msg_count
                FROM session s
                WHERE 1=1
            """
            params: list = []

            if project:
                safe_project = project.replace("%", "\\%").replace("_", "\\_")
                query += " AND s.directory LIKE ? ESCAPE '\\'"
                params.append(f"%{safe_project}%")

            if since:
                query += " AND s.time_updated > ?"
                params.append(since)

            query += " ORDER BY s.time_updated DESC"

            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        results = []
        for r in rows:
            d = dict(r)
            results.append(SessionMeta(
                id=d["id"],
                title=d.get("title", ""),
                project=d.get("directory", "").replace("\\", "/").rstrip("/").split("/")[-1],
                msg_count=d.get("msg_count", 0),
                timestamp=d.get("time_updated", 0),
            ))

        return results

    def get_session(self, session_id: str) -> Session | None:
        """Export a session via `opencode export` command and parse it."""
        opencode_bin = self._find_opencode()
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

    def count_tools(self, session_id: str) -> int:
        """Count tool-call parts for a session."""
        db_path = self._get_db()
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute(
                """
                SELECT COUNT(*) FROM part p
                WHERE p.session_id = ?
                  AND json_extract(p.data, '$.type') = 'tool'
                """,
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        return result[0] if result else 0

    @staticmethod
    def _find_opencode() -> str:
        """Find the opencode binary."""
        candidates = [
            Path.home() / "AppData" / "Roaming" / "npm" / "opencode.cmd",
            Path.home() / "AppData" / "Roaming" / "npm" / "opencode",
            Path("/usr/local/bin/opencode"),
            Path.home() / ".local" / "bin" / "opencode",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return "opencode"
