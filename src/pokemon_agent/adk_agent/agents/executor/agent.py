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
                target = action.get("target", current_world_target(state.get("observation", {})))
                result = self.client.move_to_world_cell(
                    target_x=int(target[0]),
                    target_y=int(target[1]),
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
