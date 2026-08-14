from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
from typing import Any

from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger
from pokemon_agent.adk_agent.coordinator.action_cycle import (
    action_cycle_needs_planning,
    action_transition_summary,
    append_transition,
    build_state_diff,
    goal_from_objective,
    verify_action_cycle,
    verify_goal,
)
from pokemon_agent.adk_agent.client import PokemonToolClient
from pokemon_agent.adk_agent.agents.planner.schema import (
    ActionPlanner,
    PokemonAgentState,
    classify_mode,
    initial_state,
)
from pokemon_agent.adk_agent.agents.executor.agent import ExecutionAgent
from pokemon_agent.adk_agent.agents.interpreter.agent import ResultInterpreterAgent
from pokemon_agent.adk_agent.agents.interpreter.schema import ResultSummarizer
from pokemon_agent.adk_agent.agents.planner.agent import PlanningAgent
from pokemon_agent.adk_agent.agents.shared import TraceSink
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.memory.file_memory import FileLongTermMemory


class PokemonAdkLoop:
    def __init__(
        self,
        client: PokemonToolClient,
        *,
        action_planner: ActionPlanner | None = None,
        result_interpreter: ResultSummarizer | None = None,
        memory_store: FileLongTermMemory | None = None,
        trace: TraceSink | None = None,
        action_logger: DateGroupedActionLogger | None = None,
        idle_pump: Callable[[], Any] | None = None,
        idle_pump_interval: float = 1 / 30,
        runtime_state_store: FileAgentRuntimeState | None = None,
    ):
        self.client = client
        self.action_planner = action_planner
        self.memory_store = memory_store or FileLongTermMemory()
        self.idle_pump = idle_pump
        self.idle_pump_interval = idle_pump_interval
        self.runtime_state_store = runtime_state_store
        self.planning_agent = PlanningAgent(
            memory_store=self.memory_store,
            action_planner=action_planner,
            idle_pump=idle_pump,
            idle_pump_interval=idle_pump_interval,
            trace=trace,
        )
        self.execution_agent = ExecutionAgent(client=client, trace=trace, action_logger=action_logger)
        self.result_interpreter_agent = ResultInterpreterAgent(
            memory_store=self.memory_store,
            summarizer=result_interpreter,
            idle_pump=idle_pump,
            idle_pump_interval=idle_pump_interval,
            trace=trace,
        )

    def run(
        self,
        *,
        objective: str = "safe_loop",
        max_steps: int = 20,
        checkpoint_every: int = 10,
        initial_runtime_state: PokemonAgentState | None = None,
    ) -> PokemonAgentState:
        if initial_runtime_state is None:
            state = initial_state(
                objective=objective,
                max_steps=max_steps,
                checkpoint_every=checkpoint_every,
            )
            state["current_goal"] = goal_from_objective(objective)
        else:
            state = deepcopy(initial_runtime_state)
            previous_objective = str(state.get("objective") or "safe_loop")
            requested_objective = objective or previous_objective
            state["objective"] = requested_objective
            state["max_steps"] = int(state.get("step_count", 0)) + max(0, int(max_steps))
            state["checkpoint_every"] = checkpoint_every
            state["done"] = False
            state["termination_reason"] = None
            if requested_objective != previous_objective:
                state["current_goal"] = goal_from_objective(requested_objective)
                state.pop("active_action_plan", None)
                state.pop("action_outcome", None)
                state["replan_required"] = True
            else:
                state.setdefault("current_goal", goal_from_objective(requested_objective))
        state["memory_path"] = str(self.memory_store.path)
        self._publish(state, phase="starting")

        while not state.get("done", False):
            state.update(self._observe(state))
            state.update({"mode": classify_mode(state["observation"])})
            state.update(self._verify_goal_before_action(state))
            self._publish(state, phase="observed")
            if state.get("done"):
                self._publish(state, phase="completed")
                break
            if action_cycle_needs_planning(state):
                state.update(self._plan(state))
            else:
                state["planned_action"] = dict(state["active_action_plan"]["action"])
            self._publish(state, phase="planned")
            state.update(self._act(state))
            state.update(self._verify(state))
            self._publish(state, phase="executed")
            state.update(self._interpret(state))
            self._publish(state, phase="interpreted")
            state.update(self._checkpoint(state))
            self._publish(state, phase="completed" if state.get("done") else "checkpointed")

        return state

    def _publish(self, state: PokemonAgentState, *, phase: str) -> None:
        if self.runtime_state_store is None:
            return
        self.runtime_state_store.publish(dict(state), phase=phase)

    def _observe(self, state: PokemonAgentState) -> PokemonAgentState:
        return {
            "previous_observation": state.get("observation", {}),
            "observation": self.client.observe(),
        }

    def _plan(self, state: PokemonAgentState) -> PokemonAgentState:
        return self.planning_agent.plan(state)

    def _act(self, state: PokemonAgentState) -> PokemonAgentState:
        return self.execution_agent.execute(state)

    def _verify(self, state: PokemonAgentState) -> PokemonAgentState:
        previous = state.get("observation", {})
        result = state.get("action_result", {})
        after = result.get("after_observation", previous)
        state_diff = build_state_diff(previous, after)
        goal_verification = verify_goal(state.get("current_goal", {}), after)
        goal_completed = bool(goal_verification.get("verified"))
        active_action_plan, action_outcome = verify_action_cycle(
            state.get("active_action_plan", {}),
            after_observation=after,
            action_result=result,
            state_diff=state_diff,
            goal_completed=goal_completed,
        )
        current_goal = dict(state.get("current_goal", {}))
        current_goal["verification"] = goal_verification
        current_goal["status"] = "completed" if goal_completed else "in_progress"

        before_position = position_tuple(previous)
        after_position = position_tuple(after)
        stuck_score = state.get("stuck_score", 0)
        action = state.get("planned_action", {})

        if action.get("type") == "move":
            if result.get("stop_reason") in {"no_path", "movement_blocked", "max_steps_reached", "execution_error"}:
                stuck_score += 1
            elif before_position is not None and before_position == after_position and result.get("steps_taken", 0) > 0:
                stuck_score += 1
            else:
                stuck_score = max(0, stuck_score - 1)
        else:
            stuck_score = max(0, stuck_score - 1)

        max_steps_reached = state.get("step_count", 0) >= state.get("max_steps", 20)
        done = goal_completed or max_steps_reached
        termination_reason = "goal_completed" if goal_completed else "max_steps_reached" if max_steps_reached else None
        transitions, history_summary = append_transition(
            list(state.get("transition_history", [])),
            action_transition_summary(
                active_action_plan,
                state.get("planned_action", {}),
                state_diff,
                action_outcome,
            ),
            existing_summary=state.get("history_summary"),
        )
        session_dialog = list(state.get("session_dialog", []))
        session_dialog.append(
            {
                "agent": "pokemon_red_execution_agent",
                "phase": "action_progress",
                "step": state.get("step_count", 0),
                "content": (
                    f"Action {state.get('planned_action', {}).get('type')} -> {action_outcome.get('status')}; "
                    f"state changes: {', '.join(state_diff.get('event_types', [])) or 'none'}."
                ),
                "current_goal": current_goal.get("id"),
                "action_plan": active_action_plan,
            }
        )
        session_dialog = session_dialog[-20:]

        history = list(state.get("action_history", []))
        if history:
            history[-1]["before_state"] = state_diff.get("before")
            history[-1]["after_state"] = state_diff.get("after")
            history[-1]["state_events"] = state_diff.get("events", [])
            history[-1]["state_diff"] = state_diff
            history[-1]["action_outcome"] = action_outcome
            log_path = self.execution_agent.record_verified(history[-1])
            if log_path is not None:
                history[-1]["action_log_path"] = str(log_path)
                state.get("execution_report", {})["action_log_path"] = str(log_path)
        history = history[-20:]

        return {
            "observation": after,
            "current_goal": current_goal,
            "active_action_plan": active_action_plan,
            "state_diff": state_diff,
            "action_outcome": action_outcome,
            "transition_history": transitions,
            "history_summary": history_summary,
            "action_history": history,
            "session_dialog": session_dialog,
            "replan_required": action_outcome.get("status") != "continue",
            "stuck_score": stuck_score,
            "termination_reason": termination_reason,
            "done": done,
        }

    def _verify_goal_before_action(self, state: PokemonAgentState) -> PokemonAgentState:
        goal = dict(state.get("current_goal", {}))
        verification = verify_goal(goal, state.get("observation", {}))
        goal["verification"] = verification
        if verification.get("verified"):
            goal["status"] = "completed"
            return {"current_goal": goal, "termination_reason": "goal_completed", "done": True}
        goal["status"] = "in_progress"
        return {"current_goal": goal}

    def _interpret(self, state: PokemonAgentState) -> PokemonAgentState:
        return self.result_interpreter_agent.interpret(state)

    def _checkpoint(self, state: PokemonAgentState) -> PokemonAgentState:
        every = state.get("checkpoint_every", 10)
        step_count = state.get("step_count", 0)
        if every <= 0 or step_count <= 0 or step_count % every != 0:
            return {"checkpoint_path": state.get("checkpoint_path")}
        result = self.client.save_state(kind="last")
        return {"checkpoint_path": result.get("path")}


def position_tuple(observation: dict[str, Any]) -> tuple[int, int] | None:
    position = observation.get("state", {}).get("position")
    if position is None:
        return None
    return int(position["x"]), int(position["y"])
