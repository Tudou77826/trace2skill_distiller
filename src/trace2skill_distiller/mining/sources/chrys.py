"""Chrys data source — JSON file-based session storage."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .base import SessionSource
from ..types import (
    Session,
    SessionInfo,
    SessionMeta,
    SessionSummary,
    Message,
    MessageInfo,
    TokenInfo,
)


def _sessions_dir() -> Path:
    """Return the Chrys sessions directory for the current platform."""
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return appdata / "chrys" / "sessions"
    return Path.home() / ".chrys" / "sessions"


def _parse_iso_to_ms(ts: str) -> int:
    """Parse ISO 8601 timestamp to unix milliseconds."""
    if not ts:
        return 0
    ts = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


class ChrysSource:
    """Chrys data source: reads session JSON files from disk."""

    def __init__(self, sessions_dir: str | None = None):
        self._sessions_dir = Path(sessions_dir) if sessions_dir else _sessions_dir()

    # ── SessionSource protocol ──

    def list_sessions(
        self,
        project: str | None = None,
        since: int | None = None,
    ) -> list[SessionMeta]:
        """List available sessions from Chrys session directory."""
        if not self._sessions_dir.exists():
            return []

        results = []
        for entry in self._sessions_dir.iterdir():
            if not entry.is_dir():
                continue
            session_file = entry / "session.json"
            if not session_file.exists():
                continue

            meta = self._read_meta(session_file)
            if not meta:
                continue

            cwd = meta.get("primary_cwd", "")
            project_name = cwd.replace("\\", "/").rstrip("/").split("/")[-1] if cwd else ""

            if project and project.lower() not in project_name.lower():
                continue

            ts = _parse_iso_to_ms(meta.get("updated_at", ""))
            if since and ts < since:
                continue

            results.append(SessionMeta(
                id=meta.get("session_id", entry.name),
                title=meta.get("title", ""),
                project=project_name,
                msg_count=meta.get("message_count", 0),
                timestamp=ts,
            ))

        results.sort(key=lambda s: s.timestamp, reverse=True)
        return results

    def get_session(self, session_id: str) -> Session | None:
        """Read and parse a full session from Chrys JSON file."""
        session_path = self._resolve_session_path(session_id)
        if not session_path:
            return None

        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        meta = data.get("meta", {})
        messages_raw = data.get("state", {}).get("messages", [])
        cwd = meta.get("primary_cwd", "")

        # Build call_id → result index for pairing function_call with function_result
        tool_results = _build_tool_result_index(messages_raw)

        messages = []
        for msg in messages_raw:
            parts = _convert_contents(msg.get("contents", []), tool_results)
            chrys_kind = msg.get("additional_properties", {}).get("_chrys_kind", "")

            messages.append(Message(
                info=MessageInfo(
                    role=msg.get("role", ""),
                    modelID=meta.get("model_id", ""),
                    providerID=meta.get("model_provider", ""),
                    finish=_map_chrys_kind(chrys_kind),
                    time={
                        "created": _parse_iso_to_ms(meta.get("created_at", "")),
                        "updated": _parse_iso_to_ms(meta.get("updated_at", "")),
                    },
                ),
                parts=parts,
            ))

        return Session(
            info=SessionInfo(
                id=meta.get("session_id", ""),
                slug=session_path.parent.name,
                directory=cwd,
                title=meta.get("title", ""),
                version=str(meta.get("schema_version", "")),
                time={
                    "created": _parse_iso_to_ms(meta.get("created_at", "")),
                    "updated": _parse_iso_to_ms(meta.get("updated_at", "")),
                },
            ),
            messages=messages,
        )

    def count_tools(self, session_id: str) -> int:
        """Count function_call entries for a session."""
        session_path = self._resolve_session_path(session_id)
        if not session_path:
            return 0

        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = 0
            for msg in data.get("state", {}).get("messages", []):
                for c in msg.get("contents", []):
                    if c.get("type") == "function_call":
                        count += 1
            return count
        except (json.JSONDecodeError, OSError):
            return 0

    # ── Internal helpers ──

    def _resolve_session_path(self, session_id: str) -> Path | None:
        """Resolve a session_id to its session.json path.

        Accepts both short_id (12 chars) and full UUID.
        """
        # Try as short_id (directory name)
        short = session_id[:12]
        candidate = self._sessions_dir / short / "session.json"
        if candidate.exists():
            return candidate

        # Full scan matching session_id against meta.session_id
        if not self._sessions_dir.exists():
            return None
        for entry in self._sessions_dir.iterdir():
            if not entry.is_dir():
                continue
            session_file = entry / "session.json"
            meta = self._read_meta(session_file)
            if meta and meta.get("session_id") == session_id:
                return session_file
        return None

    @staticmethod
    def _read_meta(session_path: Path) -> dict | None:
        """Read only the meta section from a session file."""
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("meta")
        except (json.JSONDecodeError, OSError):
            return None


# ── Module-level helpers ──


def _build_tool_result_index(messages: list[dict]) -> dict[str, str]:
    """Build call_id → result mapping from all messages."""
    results: dict[str, str] = {}
    for msg in messages:
        for c in msg.get("contents", []):
            if c.get("type") == "function_result":
                call_id = c.get("call_id", "")
                if call_id:
                    results[call_id] = c.get("result", "")
    return results


def _convert_contents(
    contents: list[dict],
    tool_results: dict[str, str],
) -> list[dict]:
    """Convert Chrys contents to common Session parts format."""
    parts: list[dict] = []
    for c in contents:
        ctype = c.get("type", "")

        if ctype == "text":
            parts.append({
                "type": "text",
                "text": c.get("text", ""),
            })

        elif ctype == "function_call":
            call_id = c.get("call_id", "")
            tool_name = c.get("name", "unknown")
            arguments = c.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"raw": arguments}

            result = tool_results.get(call_id)
            parts.append({
                "type": "tool",
                "callID": call_id,
                "tool": tool_name,
                "state": {
                    "status": "completed" if result is not None else "pending",
                    "input": arguments,
                    "output": result,
                },
            })

        # function_result is skipped — already paired via tool_results index

    return parts


def _map_chrys_kind(kind: str) -> str:
    """Map Chrys _chrys_kind to common finish field."""
    return {"turn": "stop", "interrupted": "error"}.get(kind, "")
