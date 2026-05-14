"""CodeAgent data source — SQLite direct read (ngagent.db)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .base import SessionSource
from ..types import Message, MessageInfo, Session, SessionInfo, SessionMeta, TokenInfo


class CodeAgentSource:
    """CodeAgent data source: reads directly from SQLite (no CLI export)."""

    def __init__(self, db_path: str = "~/.local/share/opencode/db/ngagent.db"):
        self._db_path = Path(db_path).expanduser()

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
            raise FileNotFoundError(f"CodeAgent database not found: {db_path}")

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
        """Load a session directly from SQLite (no CLI export)."""
        db_path = self._get_db()
        if not db_path.exists():
            raise FileNotFoundError(f"CodeAgent database not found: {db_path}")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        try:
            # Get session info
            session_row = conn.execute(
                """
                SELECT id, slug, project_id, directory, title, version,
                       time_created, time_updated,
                       summary_additions, summary_deletions, summary_files
                FROM session
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()

            if not session_row:
                return None

            s = dict(session_row)

            # Build SessionInfo
            info = SessionInfo(
                id=s["id"],
                slug=s.get("slug", ""),
                projectID=s.get("project_id", ""),
                directory=s.get("directory", ""),
                title=s.get("title", ""),
                version=s.get("version", ""),
                summary={
                    "additions": s.get("summary_additions", 0),
                    "deletions": s.get("summary_deletions", 0),
                    "files": s.get("summary_files", 0),
                },
                time={
                    "created": s.get("time_created", 0),
                    "updated": s.get("time_updated", 0),
                },
            )

            # Get messages (ordered by time_created)
            msg_rows = conn.execute(
                """
                SELECT id, session_id, time_created, data
                FROM message
                WHERE session_id = ?
                ORDER BY time_created ASC
                """,
                (session_id,),
            ).fetchall()

            messages = []
            for mr in msg_rows:
                m = dict(mr)
                data = json.loads(m["data"]) if m["data"] else {}

                # Parse message data JSON
                msg_info = MessageInfo(
                    role=data.get("role", "unknown"),
                    time=data.get("time", {}),
                    summary=data.get("summary", {}),
                    agent=data.get("agent", ""),
                    modelID=data.get("modelID", ""),
                    providerID=data.get("providerID", ""),
                    mode=data.get("mode", ""),
                    cost=data.get("cost", 0),
                    tokens=TokenInfo(**data.get("tokens", {})),
                    finish=data.get("finish", ""),
                    parentID=data.get("parentID", ""),
                    path=data.get("path", {}),
                    id=m.get("id", ""),
                    sessionID=m.get("session_id", ""),
                    error=data.get("error"),
                )

                # Get parts for this message
                part_rows = conn.execute(
                    """
                    SELECT data
                    FROM part
                    WHERE message_id = ?
                    """,
                    (m["id"],),
                ).fetchall()

                parts = []
                for pr in part_rows:
                    part_data = json.loads(pr["data"]) if pr["data"] else {}
                    parts.append(part_data)

                messages.append(Message(info=msg_info, parts=parts))

            return Session(info=info, messages=messages)
        finally:
            conn.close()

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