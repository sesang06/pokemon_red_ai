from __future__ import annotations

from typing import Any

from pokemon_agent.adk_agent.agents.planner.schema import PokemonAgentState
from pokemon_agent.adk_agent.client import PokemonToolClient


ALLOWED_EXECUTION_ACTIONS = {"buttons", "move"}


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    compact.pop("before_observation", None)
    compact.pop("after_observation", None)
    return compact


def compact_plan_decision(plan_decision: Any) -> dict[str, Any]:
    if not isinstance(plan_decision, dict):
        return {}
    return {
        "agent": plan_decision.get("agent"),
        "phase": plan_decision.get("phase"),
        "action_plan": plan_decision.get("action_plan"),
        "memory_keys_read": plan_decision.get("memory_keys_read"),
        "reason": plan_decision.get("reason"),
    }


def execution_error_result(
    exc: Exception,
    *,
    client: PokemonToolClient,
    state: PokemonAgentState,
) -> dict[str, Any]:
    before = state.get("observation", {})
    try:
        after = client.observe()
    except Exception:
        after = before
    return {
        "stop_reason": "execution_error",
        "error": f"{type(exc).__name__}: {exc}",
        "executed_actions": [],
        "steps_taken": 0,
        "before_observation": before,
        "after_observation": after,
    }


def success_hint(action: dict[str, Any], result: dict[str, Any]) -> str:
    if result.get("stop_reason") == "target_reached":
        return "target_reached"
    if result.get("steps_taken", 0):
        return "movement_progress"
    if action.get("type") == "buttons" and action.get("buttons") == ["wait"]:
        return "realtime_wait_complete"
    if action.get("type") == "buttons" and result.get("executed_actions"):
        return "buttons_pressed"
    return "observe_after_action"


def compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": observation.get("state"),
        "tool_step_index": observation.get("tool_step_index"),
        "frame_index": observation.get("frame_index"),
        "visible_world_cells": observation.get("visible_world_cells"),
        "safe_neighbor_world_cells": observation.get("safe_neighbor_world_cells"),
        "world_map": observation.get("world_map"),
    }


def current_world_target(observation: dict[str, Any]) -> list[int]:
    position = observation.get("state", {}).get("position") if isinstance(observation, dict) else None
    if isinstance(position, dict):
        return [int(position.get("x", 0)), int(position.get("y", 0))]
    return [0, 0]
