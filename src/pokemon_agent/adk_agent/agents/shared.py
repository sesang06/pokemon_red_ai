from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


TraceSink = Callable[[dict[str, Any]], None]


class ConsoleTokenStream:
    def __init__(self, label: str, *, enabled: bool) -> None:
        self.label = label
        self.enabled = enabled
        self.started = False

    def write(self, value: str) -> None:
        if not self.enabled or not value:
            return
        if not self.started:
            print(f"[llm-stream {self.label}] ", end="", flush=True)
            self.started = True
        print(value, end="", flush=True)

    def finish(self, final_text: str) -> None:
        if not self.enabled:
            return
        if not self.started and final_text:
            self.write(final_text)
        if self.started:
            print(flush=True)


def event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    if content is None:
        output = getattr(event, "output", None)
        return "" if output is None else str(output)

    parts = getattr(content, "parts", None) or []
    text_parts = [str(part.text) for part in parts if getattr(part, "text", None)]
    return "\n".join(text_parts)


def event_finish_reason(event: Any) -> str | None:
    reason = getattr(event, "finish_reason", None)
    if reason is None:
        return None
    value = getattr(reason, "value", None)
    return str(value if value is not None else reason)


def invalid_response_error(content: str, *, finish_reason: str | None) -> str:
    response_kind = "empty_response" if not content.strip() else "invalid_json_response"
    reason = finish_reason or "unknown"
    return f"{response_kind} (finish_reason={reason}, chars={len(content)})"


def parse_json_object(content: str) -> Any:
    cleaned = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def emit_trace(trace: TraceSink | None, event: dict[str, Any]) -> None:
    if trace is not None:
        trace(event)


def run_with_idle_pump(
    call: Callable[[], Any],
    *,
    idle_pump: Callable[[], Any] | None,
    idle_pump_interval: float,
    on_wait: Callable[[float], None] | None = None,
    wait_trace_interval: float = 1.0,
) -> Any:
    if idle_pump is None:
        return call()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(call)
        started_at = time.monotonic()
        last_wait_trace = started_at
        while not future.done():
            idle_pump()
            now = time.monotonic()
            if on_wait is not None and now - last_wait_trace >= wait_trace_interval:
                on_wait(now - started_at)
                last_wait_trace = now
            time.sleep(max(0.001, idle_pump_interval))
        return future.result()
