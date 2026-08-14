from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass
class McpCommandLogEntry:
    id: int
    timestamp: str
    tool: str
    args: dict[str, Any]
    status: str = "received"
    result_summary: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "args": self.args,
            "status": self.status,
            "result_summary": self.result_summary,
            "error": self.error,
        }


class McpCommandLog:
    def __init__(self, max_entries: int = 100):
        self._entries: deque[McpCommandLogEntry] = deque(maxlen=max_entries)
        self._next_id = 1
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._next_id = 1

    def started(self, tool: str, args: dict[str, Any]) -> int:
        with self._lock:
            entry_id = self._next_id
            self._next_id += 1
            entry = McpCommandLogEntry(
                id=entry_id,
                timestamp=datetime.now().isoformat(timespec="seconds"),
                tool=tool,
                args={key: _sanitize_value(value) for key, value in args.items()},
            )
            self._entries.append(entry)

        LOGGER.info("MCP received #%s %s %s", entry_id, tool, _format_args(entry.args))
        return entry_id

    def completed(self, entry_id: int, result: Any) -> None:
        summary = summarize_result(result)
        self._update(entry_id, status="ok", result_summary=summary)
        LOGGER.info("MCP completed #%s %s", entry_id, summary)

    def failed(self, entry_id: int, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"
        self._update(entry_id, status="error", error=error)
        LOGGER.error("MCP failed #%s %s", entry_id, error)
        LOGGER.debug("MCP failure details", exc_info=True)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            entries = list(self._entries)[-limit:]
            return [entry.as_dict() for entry in entries]

    def format_recent(self, limit: int = 50) -> str:
        entries = self.recent(limit)
        if not entries:
            return "Waiting for MCP tool calls..."

        lines: list[str] = []
        for entry in entries:
            timestamp = str(entry["timestamp"]).split("T")[-1]
            args = _format_args(entry["args"])
            line = f"{timestamp} #{entry['id']} {entry['status']} {entry['tool']} {args}"
            if entry.get("result_summary"):
                line = f"{line} -> {entry['result_summary']}"
            if entry.get("error"):
                line = f"{line} -> {entry['error']}"
            lines.append(line)
        return "\n".join(lines)

    def _update(
        self,
        entry_id: int,
        *,
        status: str,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            for entry in reversed(self._entries):
                if entry.id == entry_id:
                    entry.status = status
                    entry.result_summary = result_summary
                    entry.error = error
                    return


def summarize_result(result: Any) -> str:
    if not isinstance(result, dict):
        return type(result).__name__

    if "waited" in result:
        return _trim(
            f"waited={result.get('waited')}, elapsed_ms={result.get('elapsed_ms')}, "
            f"stop_reason={result.get('stop_reason')}"
        )

    if "stop_reason" in result:
        actions = _action_buttons(result.get("executed_actions"))
        return _trim(
            f"stop_reason={result.get('stop_reason')}, "
            f"steps_taken={result.get('steps_taken')}, actions={actions}"
        )

    if "executed_actions" in result:
        return _trim(f"actions={_action_buttons(result.get('executed_actions'))}")

    if "screenshot" in result:
        state = result.get("state", {})
        return _trim(
            f"frame={result.get('frame_index')}, "
            f"map={state.get('map_name')}, mode={state.get('mode')}"
        )

    if result.get("started") is True:
        return f"started control_ui={result.get('control_ui')}"

    if result.get("stopped") is True:
        return f"stopped saved_path={result.get('saved_path')}"

    if result.get("saved") is True:
        return f"saved {result.get('kind')} {result.get('path')}"

    if result.get("loaded") is True:
        return f"loaded {result.get('kind')} {result.get('path')}"

    if "commands" in result:
        return f"commands={len(result.get('commands') or [])}"

    if {"enabled", "fps", "frame_index"}.issubset(result.keys()):
        return (
            f"realtime enabled={result.get('enabled')}, "
            f"fps={result.get('fps')}, frame={result.get('frame_index')}"
        )

    return "keys=" + ",".join(str(key) for key in result.keys())


def _action_buttons(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    buttons: list[str] = []
    for action in value[:16]:
        if isinstance(action, dict) and "button" in action:
            buttons.append(str(action["button"]))
    return buttons


def _sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return f"<{type(value).__name__}>"

    if isinstance(value, str):
        if len(value) > 180:
            return f"<str len={len(value)}>"
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                sanitized["..."] = f"{len(value) - index} more"
                break
            key_text = str(key)
            if key_text.lower() == "base64":
                sanitized[key_text] = f"<base64 len={len(str(item))}>"
            else:
                sanitized[key_text] = _sanitize_value(item, depth=depth + 1)
        return sanitized

    if isinstance(value, (list, tuple)):
        if len(value) > 20:
            return f"<{type(value).__name__} len={len(value)}>"
        return [_sanitize_value(item, depth=depth + 1) for item in value]

    return str(value)


def _format_args(args: dict[str, Any]) -> str:
    if not args:
        return "()"
    parts = [f"{key}={_format_value(value)}" for key, value in args.items()]
    return "(" + ", ".join(parts) + ")"


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, bool)) or value is None:
        return json.dumps(value)
    return _trim(json.dumps(value, ensure_ascii=False, default=str), max_chars=220)


def _trim(value: str, max_chars: int = 260) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."
