from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pokemon_agent.adk_agent.agents.executor.schema import (
    ALLOWED_EXECUTION_ACTIONS,
    compact_plan_decision,
    compact_result,
    current_world_target,
    execution_error_result,
    success_hint,
)
from pokemon_agent.adk_agent.agents.planner.schema import PokemonAgentState, sanitize_planned_action
from pokemon_agent.adk_agent.agents.shared import TraceSink, emit_trace
from pokemon_agent.adk_agent.client import PokemonToolClient
from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger


@dataclass
class ExecutionAgent:
    client: PokemonToolClient
    trace: TraceSink | None = None
    action_logger: DateGroupedActionLogger | None = None
    name: str = "pokemon_red_execution_agent"

    def execute(self, state: PokemonAgentState) -> PokemonAgentState:
        action = sanitize_planned_action(state.get("planned_action"))
        if action is None or action.get("type") not in ALLOWED_EXECUTION_ACTIONS:
            action = {
                "type": "buttons",
                "buttons": ["wait"],
                "reason": "invalid_execution_action",
                "source": "execution_guard",
            }

        try:
            action_type = action.get("type")
            if action_type == "buttons":
                result = self.client.press_buttons([str(button) for button in action.get("buttons", [])])
            elif action_type == "move":
                result = _execute_move_route(
                    self.client,
                    action,
                    initial_observation=state.get("observation", {}),
                )
            else:
                result = self.client.wait()
        except Exception as exc:
            result = execution_error_result(exc, client=self.client, state=state)
        result = dict(result)
        result["action"] = dict(action)

        report = {
            "agent": self.name,
            "phase": "execution",
            "action": action,
            "state": result.get("after_observation", {}).get("state", {}),
            "state_events": result.get("after_observation", {}).get("state_events", []),
            "result": compact_result(result),
            "stop_reason": result.get("stop_reason"),
            "success_hint": success_hint(action, result),
        }
        emit_trace(
            self.trace,
            {
                "agent": self.name,
                "phase": "execution_done",
                "step": state.get("step_count", 0),
                "action": action,
                "stop_reason": report["stop_reason"],
                "success_hint": report["success_hint"],
                "error": result.get("error"),
            },
        )
        history = list(state.get("action_history", []))
        history.append(
            {
                "step": state.get("step_count", 0),
                "agent": self.name,
                "phase": "execution",
                "plan_decision": compact_plan_decision(state.get("plan_decision", {})),
                "action": action,
                "result": compact_result(result),
            }
        )
        return {
            "planned_action": action,
            "execution_report": report,
            "action_result": result,
            "action_history": history,
            "step_count": state.get("step_count", 0) + 1,
        }

    def record_verified(self, history_entry: dict[str, Any]) -> Any:
        if self.action_logger is None:
            return None
        return self.action_logger.append(history_entry)


def _execute_move_route(
    client: PokemonToolClient,
    action: dict[str, Any],
    *,
    initial_observation: dict[str, Any],
) -> dict[str, Any]:
    target = action.get("target", current_world_target(initial_observation))
    waypoints = [list(point) for point in action.get("waypoints", [])]
    if not waypoints:
        return client.move_to_world_cell(target_x=int(target[0]), target_y=int(target[1]))

    destinations = [("waypoint", point) for point in waypoints]
    destinations.append(("target", list(target)))
    first_before: dict[str, Any] = initial_observation
    last_result: dict[str, Any] = {}
    all_actions: list[dict[str, Any]] = []
    all_planned_path: list[dict[str, Any]] = []
    all_navigation_segments: list[dict[str, Any]] = []
    call_results: list[dict[str, Any]] = []
    route_results: list[dict[str, Any]] = []
    completed_waypoints = 0
    final_target_attempted = False

    for route_index, (kind, point) in enumerate(destinations):
        result = dict(client.move_to_world_cell(target_x=int(point[0]), target_y=int(point[1])))
        call_results.append(result)
        if route_index == 0 and isinstance(result.get("before_observation"), dict):
            first_before = result["before_observation"]
        last_result = result
        stop_reason = str(result.get("stop_reason") or "no_path")
        reached = stop_reason == "target_reached"
        if kind == "waypoint" and reached:
            completed_waypoints += 1
        if kind == "target":
            final_target_attempted = True

        for segment in result.get("navigation_segments", []):
            if not isinstance(segment, dict):
                continue
            tagged_segment = dict(segment)
            tagged_segment["index"] = len(all_navigation_segments)
            tagged_segment["route_target_index"] = route_index
            tagged_segment["route_target_kind"] = kind
            all_navigation_segments.append(tagged_segment)
        all_actions.extend(
            dict(executed)
            for executed in result.get("executed_actions", [])
            if isinstance(executed, dict)
        )
        all_planned_path.extend(
            dict(path_point)
            for path_point in result.get("planned_path", [])
            if isinstance(path_point, dict)
        )
        route_results.append(
            {
                "index": route_index,
                "kind": kind,
                "target": [int(point[0]), int(point[1])],
                "reached": reached,
                "stop_reason": stop_reason,
                "steps_taken": int(result.get("steps_taken", 0)),
                "resolved_world_cell": result.get("resolved_world_cell"),
            }
        )
        if not reached:
            break

    combined = dict(last_result)
    combined.update(
        {
            "requested_waypoints": waypoints,
            "requested_final_world_cell": {"x": int(target[0]), "y": int(target[1])},
            "completed_waypoints": completed_waypoints,
            "final_target_attempted": final_target_attempted,
            "final_target_reached": bool(
                final_target_attempted and last_result.get("stop_reason") == "target_reached"
            ),
            "route_results": route_results,
            "planned_path": all_planned_path,
            "executed_actions": all_actions,
            "steps_taken": len(all_actions),
            "navigation_segments": all_navigation_segments,
            "navigation_replans": sum(
                int(result.get("navigation_replans", 0))
                for result in call_results
            ),
            "target_out_of_visible_area": any(
                bool(entry.get("target_out_of_visible_area"))
                for entry in call_results
            ),
            "requested_target_reached": bool(
                final_target_attempted
                and last_result.get(
                    "requested_target_reached",
                    last_result.get("stop_reason") == "target_reached",
                )
            ),
            "resolved_target_reached": bool(
                final_target_attempted
                and last_result.get(
                    "resolved_target_reached",
                    last_result.get("stop_reason") == "target_reached",
                )
            ),
            "before_observation": first_before,
            "after_observation": last_result.get("after_observation", first_before),
        }
    )
    return combined
