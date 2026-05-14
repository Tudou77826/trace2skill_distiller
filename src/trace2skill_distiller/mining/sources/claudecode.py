"""Claude Code data source — JSONL files (~/.claude/projects/)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .base import SessionSource
from ..types import Message, MessageInfo, Session, SessionInfo, SessionMeta, TokenInfo


class ClaudeCodeSource:
    """Claude Code data source: reads JSONL files from ~/.claude/projects/."""

    def __init__(self, projects_dir: str = "~/.claude/projects"):
        self._projects_dir = Path(projects_dir).expanduser()

    def _get_projects_dir(self) -> Path:
        return self._projects_dir

    def _parse_iso_to_ms(self, iso_str: str) -> int:
        """Parse ISO 8601 timestamp to Unix milliseconds."""
        try:
            # Handle Z suffix
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso_str)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    def _extract_project_name(self, project_dir_name: str) -> str:
        """Extract project name from directory name like 'D--dev-workspace-ai-trace2skill-distiller'."""
        # Remove drive prefix like 'D--' if present
        parts = project_dir_name.split("--")
        if len(parts) > 1:
            return parts[-1].replace("-", "/").split("/")[-1]
        return project_dir_name.replace("-", "/").split("/")[-1]

    def _iter_session_files(self) -> list[tuple[Path, str, str]]:
        """Iterate all session JSONL files: (jsonl_path, session_id, project_name)."""
        projects_dir = self._get_projects_dir()
        if not projects_dir.exists():
            return []

        results = []
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            project_name = self._extract_project_name(project_dir.name)
            for jsonl_file in project_dir.glob("*.jsonl"):
                # Session ID is the filename without extension
                session_id = jsonl_file.stem
                results.append((jsonl_file, session_id, project_name))
        return results

    def list_sessions(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        """List sessions from JSONL files, optionally filtered."""
        projects_dir = self._get_projects_dir()
        if not projects_dir.exists():
            raise FileNotFoundError(f"Claude Code projects directory not found: {projects_dir}")

        sessions = self._iter_session_files()
        results = []

        for jsonl_path, session_id, project_name in sessions:
            # Filter by project name
            if project and project.lower() not in project_name.lower():
                continue

            # Get timestamp from first user message or file mtime
            timestamp = int(jsonl_path.stat().st_mtime * 1000)

            # Count messages by scanning file
            msg_count = 0
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get("type") in ("user", "assistant"):
                            msg_count += 1
                            # Get earliest timestamp for filtering
                            ts = d.get("timestamp", "")
                            if ts:
                                ms = self._parse_iso_to_ms(ts)
                                if ms > 0 and (timestamp == 0 or ms < timestamp):
                                    timestamp = ms
                    except json.JSONDecodeError:
                        pass

            # Filter by since
            if since and timestamp < since:
                continue

            # Get title from ai-title event
            title = ""
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get("type") == "ai-title":
                            title = d.get("title", "")
                            break
                    except json.JSONDecodeError:
                        pass

            results.append(SessionMeta(
                id=session_id,
                title=title,
                project=project_name,
                msg_count=msg_count,
                timestamp=timestamp,
            ))

        # Sort by timestamp descending
        results.sort(key=lambda x: x.timestamp, reverse=True)
        return results

    def get_session(self, session_id: str) -> Session | None:
        """Load a session from JSONL file."""
        projects_dir = self._get_projects_dir()
        if not projects_dir.exists():
            raise FileNotFoundError(f"Claude Code projects directory not found: {projects_dir}")

        # Find the JSONL file
        jsonl_path = None
        project_name = ""
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                jsonl_path = candidate
                project_name = self._extract_project_name(project_dir.name)
                break

        if not jsonl_path:
            return None

        # Parse JSONL
        events = []
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        if not events:
            return None

        # Extract session info
        first_event = events[0]
        timestamp = first_event.get("timestamp", "")
        time_created = self._parse_iso_to_ms(timestamp)
        time_updated = time_created

        # Find latest timestamp
        for e in events:
            ts = e.get("timestamp", "")
            if ts:
                ms = self._parse_iso_to_ms(ts)
                if ms > time_updated:
                    time_updated = ms

        info = SessionInfo(
            id=session_id,
            slug=session_id[:12],
            projectID=project_name,
            directory=jsonl_path.parent.name.replace("--", "/").replace("-", "/"),
            title="",
            version=first_event.get("version", ""),
            summary={},
            time={"created": time_created, "updated": time_updated},
        )

        # Find ai-title
        for e in events:
            if e.get("type") == "ai-title":
                info.title = e.get("title", "")
                break

        # Build messages
        messages = []
        for e in events:
            if e.get("type") not in ("user", "assistant"):
                continue

            msg = e.get("message", {})
            role = msg.get("role", e.get("type"))
            content = msg.get("content", [])
            usage = msg.get("usage", {})

            # Build parts
            parts = []
            if isinstance(content, str):
                parts.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type", "")
                    if ctype == "text":
                        parts.append({"type": "text", "text": c.get("text", "")})
                    elif ctype == "thinking":
                        parts.append({"type": "reasoning", "text": c.get("thinking", "")})
                    elif ctype == "tool_use":
                        parts.append({
                            "type": "tool",
                            "callID": c.get("id", ""),
                            "tool": c.get("name", ""),
                            "state": {
                                "status": "pending",
                                "input": c.get("input", {}),
                                "output": None,
                            },
                        })
                    elif ctype == "tool_result":
                        # Find matching tool_use and update its state
                        tool_use_id = c.get("tool_use_id", "")
                        for p in parts:
                            if p.get("type") == "tool" and p.get("callID") == tool_use_id:
                                p["state"]["status"] = "completed"
                                p["state"]["output"] = c.get("content", "")
                                break

            # For user messages, also check for tool_result in content
            if role == "user" and isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        # Add tool result as a separate part (will be paired downstream)
                        parts.append({
                            "type": "tool_result",
                            "callID": c.get("tool_use_id", ""),
                            "content": c.get("content", ""),
                        })

            msg_info = MessageInfo(
                role=role,
                time={"created": self._parse_iso_to_ms(e.get("timestamp", ""))},
                modelID=msg.get("model", ""),
                tokens=TokenInfo(
                    input=usage.get("input_tokens", 0),
                    output=usage.get("output_tokens", 0),
                    total=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                ),
                finish="",  # Claude Code doesn't expose finish reason
                id=e.get("uuid", ""),
                sessionID=e.get("sessionId", session_id),
            )

            messages.append(Message(info=msg_info, parts=parts))

        # Pair tool_use with tool_result
        self._pair_tool_results(messages)

        return Session(info=info, messages=messages)

    def _pair_tool_results(self, messages: list[Message]) -> None:
        """Pair tool_use in assistant messages with tool_result in subsequent user messages."""
        # Build index: callID -> tool_result content
        tool_results: dict[str, str] = {}
        for msg in messages:
            if msg.info.role == "user":
                for p in msg.parts:
                    if p.get("type") == "tool_result":
                        tool_results[p.get("callID", "")] = p.get("content", "")

        # Update tool_use parts in assistant messages
        for msg in messages:
            if msg.info.role == "assistant":
                for p in msg.parts:
                    if p.get("type") == "tool":
                        call_id = p.get("callID", "")
                        if call_id in tool_results:
                            p["state"]["status"] = "completed"
                            p["state"]["output"] = tool_results[call_id]

        # Remove tool_result parts from user messages (they're now paired)
        for msg in messages:
            if msg.info.role == "user":
                msg.parts = [p for p in msg.parts if p.get("type") != "tool_result"]

    def count_tools(self, session_id: str) -> int:
        """Count tool-call parts for a session."""
        projects_dir = self._get_projects_dir()
        if not projects_dir.exists():
            raise FileNotFoundError(f"Claude Code projects directory not found: {projects_dir}")

        # Find the JSONL file
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl_path = project_dir / f"{session_id}.jsonl"
            if jsonl_path.exists():
                count = 0
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            if d.get("type") == "assistant":
                                msg = d.get("message", {})
                                content = msg.get("content", [])
                                if isinstance(content, list):
                                    for c in content:
                                        if isinstance(c, dict) and c.get("type") == "tool_use":
                                            count += 1
                        except json.JSONDecodeError:
                            pass
                return count

        return 0