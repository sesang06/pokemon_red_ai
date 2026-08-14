from __future__ import annotations

from typing import Any, Protocol


class ResultSummarizer(Protocol):
    def summarize(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Interpret an action-cycle boundary and return durable memory candidates."""
