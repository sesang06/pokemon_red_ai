from __future__ import annotations

from typing import Any

from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState


def agent_runtime_status() -> dict[str, Any]:
    """Return the latest autoplay phase and summary shared with ADK Dev UI."""

    state = _runtime_store().read()
    return {
        key: state.get(key)
        for key in (
            "updated_at",
            "phase",
            "metadata",
            "goal",
            "active_action_plan",
            "action_outcome",
            "state_diff",
            "planner_call_count",
            "llm_planner_call_count",
            "interpreter_call_count",
            "done",
            "step_count",
            "mode",
            "stuck_score",
            "history_summary",
            "plan_decision",
            "execution_report",
            "interpretation",
            "plan_error",
            "interpret_error",
        )
    }


def recent_agent_actions(limit: int = 20) -> dict[str, Any]:
    """Return recent structured autoplay action history entries."""

    state = _runtime_store().read()
    bounded_limit = max(1, min(int(limit), 20))
    history = list(state.get("action_history", []))[-bounded_limit:]
    return {
        "updated_at": state.get("updated_at"),
        "phase": state.get("phase"),
        "count": len(history),
        "actions": history,
    }


def _runtime_store() -> FileAgentRuntimeState:
    return FileAgentRuntimeState()
