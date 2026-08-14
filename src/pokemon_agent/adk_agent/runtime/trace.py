from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO


@dataclass
class AgentTracePrinter:
    enabled: bool = True
    stream: TextIO | None = None

    def __call__(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        stream = self.stream or sys.stdout
        print(format_trace_event(event), file=stream, flush=True)


def format_trace_event(event: dict[str, Any]) -> str:
    agent = event.get("agent", "pokemon_agent")
    phase = event.get("phase", "unknown")
    step = event.get("step")
    header = f"[agent-trace] {agent} phase={phase}"
    if step is not None:
        header += f" step={step}"

    lines = [header]
    for key in (
        "screen_description",
        "current_location",
        "thought_summary",
        "elapsed_seconds",
        "memory_keys_read",
        "action",
        "stop_reason",
        "success_hint",
        "memory_written",
    ):
        value = event.get(key)
        if value is not None:
            lines.append(f"{key}: {_compact_json(value)}")

    error = event.get("error")
    if error:
        lines.append(f"error: {error}")
    return "\n".join(lines)


def _compact_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
