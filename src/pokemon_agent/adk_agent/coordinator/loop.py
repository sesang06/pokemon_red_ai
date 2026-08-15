from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
from typing import Any

from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger
from pokemon_agent.adk_agent.coordinator.action_cycle import (
    action_transition_summary,
    append_transition,
    build_state_diff,
    verify_action_cycle,
)
from pokemon_agent.adk_agent.client import PokemonToolClient
from pokemon_agent.adk_agent.agents.planner.schema import (
    ActionPlanner,
    DEFAULT_MAX_STEPS,
    PokemonAgentState,
    classify_mode,
    initial_state,
)
from pokemon_agent.adk_agent.agents.goal import goal_from_main, normalize_goal
from pokemon_agent.adk_agent.agents.executor.agent import ExecutionAgent
from pokemon_agent.adk_agent.agents.interpreter.agent import ResultInterpreterAgent
from pokemon_agent.adk_agent.agents.interpreter.schema import ResultSummarizer
from pokemon_agent.adk_agent.agents.planner.agent import PlanningAgent
from pokemon_agent.adk_agent.agents.shared import TraceSink
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.adk_agent.runtime.history import RAW_HISTORY_TURNS
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
            action_planner=action_planner,
            idle_pump=idle_pump,
            idle_pump_interval=idle_pump_interval,
            trace=trace,
        )
        self.execution_agent = ExecutionAgent(client=client, trace=trace, action_logger=action_logger)
        self.result_interpreter_agent = ResultInterpreterAgent(
            summarizer=result_interpreter,
            idle_pump=idle_pump,
            idle_pump_interval=idle_pump_interval,
            trace=trace,
        )

    def run(
        self,
        *,
        main_goal: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        checkpoint_every: int = 10,
        initial_runtime_state: PokemonAgentState | None = None,
    ) -> PokemonAgentState:
        state = self.initialize_state(
            main_goal=main_goal,
            max_steps=max_steps,
            checkpoint_every=checkpoint_every,
            initial_runtime_state=initial_runtime_state,
        )
        self._publish(state, phase="starting")

        while not state.get("done", False):
            state.update(self._observe(state))
            state.update({"mode": classify_mode(state["observation"])})
            self._publish(state, phase="observed")
            if state.get("done"):
                self._publish(state, phase="completed")
                break
            state.update(self._plan(state))
            self._publish(state, phase="planned")
            if state.get("done"):
                self._publish(state, phase="completed")
                break
            state.update(self._act(state))
            state.update(self._verify(state))
            self._publish(state, phase="executed")
            state.update(self._interpret(state))
            self._publish(state, phase="interpreted")
            state.update(self._checkpoint(state))
            self._publish(state, phase="completed" if state.get("done") else "checkpointed")

        return state

    def initialize_state(
        self,
        *,
        main_goal: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        checkpoint_every: int = 10,
        initial_runtime_state: PokemonAgentState | None = None,
    ) -> PokemonAgentState:
        if initial_runtime_state is None:
            restored = self._read_runtime_goal()
            selected_goal = goal_from_main(main_goal) if main_goal is not None else normalize_goal(restored)
            state = initial_state(
                goal=selected_goal,
                max_steps=max_steps,
                checkpoint_every=checkpoint_every,
            )
        else:
            state = deepcopy(initial_runtime_state)
            previous_goal = normalize_goal(state.get("goal"))
            selected_goal = goal_from_main(main_goal) if main_goal is not None else previous_goal
            state["goal"] = selected_goal
            state["max_steps"] = int(state.get("step_count", 0)) + max(0, int(max_steps))
            state["checkpoint_every"] = checkpoint_every
            state["done"] = False
            state["termination_reason"] = None
            if selected_goal != previous_goal:
                state.pop("active_action_plan", None)
                state.pop("action_outcome", None)
        state["memory_path"] = str(self.memory_store.path)
        return state

    def _read_runtime_goal(self) -> dict[str, str] | None:
        if self.runtime_state_store is None:
            return None
        reader = getattr(self.runtime_state_store, "read", None)
        if not callable(reader):
            return None
        saved = reader()
        goal = saved.get("goal") if isinstance(saved, dict) else None
        return normalize_goal(goal) if isinstance(goal, dict) else None

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
        active_action_plan, action_outcome = verify_action_cycle(
            state.get("active_action_plan", {}),
            action_result=result,
            state_diff=state_diff,
        )

        before_position = position_tuple(previous)
        after_position = position_tuple(after)
        stuck_score = state.get("stuck_score", 0)
        active_plan = state.get("active_action_plan", {})
        action = active_plan.get("action", {}) if isinstance(active_plan, dict) else {}

        if action.get("type") == "move":
            if result.get("stop_reason") in {"no_path", "movement_blocked", "max_steps_reached", "execution_error"}:
                stuck_score += 1
            elif before_position is not None and before_position == after_position and result.get("steps_taken", 0) > 0:
                stuck_score += 1
            else:
                stuck_score = max(0, stuck_score - 1)
        else:
            stuck_score = max(0, stuck_score - 1)

        max_steps_reached = state.get("step_count", 0) >= state.get("max_steps", DEFAULT_MAX_STEPS)
        done = max_steps_reached
        termination_reason = "max_steps_reached" if max_steps_reached else None
        transitions, history_summary = append_transition(
            list(state.get("transition_history", [])),
            action_transition_summary(
                action,
                state_diff,
                action_outcome,
            ),
            existing_summary=state.get("history_summary"),
        )
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
        history = history[-RAW_HISTORY_TURNS:]

        return {
            "observation": after,
            "goal": normalize_goal(state.get("goal")),
            "active_action_plan": active_action_plan,
            "state_diff": state_diff,
            "action_outcome": action_outcome,
            "transition_history": transitions,
            "history_summary": history_summary,
            "action_history": history,
            "stuck_score": stuck_score,
            "termination_reason": termination_reason,
            "done": done,
        }

    def _interpret(self, state: PokemonAgentState) -> PokemonAgentState:
        return self.result_interpreter_agent.interpret(state)

    def _checkpoint(self, state: PokemonAgentState) -> PokemonAgentState:
        fixed_result = self.client.save_state(kind="fixed")
        updates: PokemonAgentState = {
            "fixed_state_path": fixed_result.get("path"),
            "checkpoint_path": state.get("checkpoint_path"),
        }

        every = state.get("checkpoint_every", 10)
        step_count = state.get("step_count", 0)
        if every <= 0 or step_count <= 0 or step_count % every != 0:
            return updates
        result = self.client.save_state(kind="last")
        updates["checkpoint_path"] = result.get("path")
        return updates


def position_tuple(observation: dict[str, Any]) -> tuple[int, int] | None:
    position = observation.get("state", {}).get("position")
    if position is None:
        return None
    return int(position["x"]), int(position["y"])
