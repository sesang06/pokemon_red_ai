from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUNTIME_STATE_PATH = PROJECT_ROOT / "data" / "adk_runtime_state.json"


@dataclass
class FileAgentRuntimeState:
    path: Path = DEFAULT_RUNTIME_STATE_PATH
    metadata: dict[str, Any] = field(default_factory=dict)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "updated_at": None,
                "phase": "not_started",
                "action_history": [],
                "session_dialog": [],
            }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "version": 1,
                "updated_at": None,
                "phase": "unavailable",
                "action_history": [],
                "session_dialog": [],
            }
        return data if isinstance(data, dict) else {}

    def publish(self, state: dict[str, Any], *, phase: str) -> None:
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "phase": phase,
            "metadata": dict(self.metadata),
            "objective": state.get("objective"),
            "current_goal": state.get("current_goal"),
            "active_action_plan": state.get("active_action_plan"),
            "action_outcome": state.get("action_outcome"),
            "state_diff": state.get("state_diff"),
            "transition_history": list(state.get("transition_history", [])),
            "replan_required": state.get("replan_required", False),
            "planner_call_count": state.get("planner_call_count", 0),
            "llm_planner_call_count": state.get("llm_planner_call_count", 0),
            "interpreter_call_count": state.get("interpreter_call_count", 0),
            "termination_reason": state.get("termination_reason"),
            "done": state.get("done", False),
            "step_count": state.get("step_count", 0),
            "mode": state.get("mode"),
            "stuck_score": state.get("stuck_score", 0),
            "history_summary": state.get("history_summary"),
            "action_history": list(state.get("action_history", [])),
            "session_dialog": list(state.get("session_dialog", [])),
            "plan_decision": state.get("plan_decision"),
            "execution_report": state.get("execution_report"),
            "interpretation": state.get("interpretation"),
            "plan_error": state.get("plan_error"),
            "interpret_error": state.get("interpret_error"),
        }
        self._write_atomic(payload)

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            for attempt in range(5):
                try:
                    os.replace(temp_name, self.path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
