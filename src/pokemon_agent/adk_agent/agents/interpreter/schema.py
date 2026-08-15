from __future__ import annotations

from typing import Any, Protocol, TypedDict


class InterpreterResponse(TypedDict):
    screen_description: str
    current_location: str
    thought_summary: str
    summary: str
    memory_saved: bool


class ResultSummarizer(Protocol):
    def summarize(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Interpret an action failure or durable event, using bound memory tools when needed."""
