from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pokemon_agent.adk_agent.client import PokemonToolClient
from pokemon_agent.adk_agent.planning import (
    ActionPlanner,
    PokemonAgentState,
    classify_mode,
    initial_state,
    plan_next_action,
    rule_based_plan,
    sanitize_planned_action,
)


class PokemonAdkLoop:
    def __init__(
        self,
        client: PokemonToolClient,
        *,
        action_planner: ActionPlanner | None = None,
        idle_pump: Callable[[], Any] | None = None,
        idle_pump_interval: float = 1 / 30,
    ):
        self.client = client
        self.action_planner = action_planner
        self.idle_pump = idle_pump
        self.idle_pump_interval = idle_pump_interval

    def run(
        self,
        *,
        objective: str = "safe_loop",
        max_steps: int = 20,
        checkpoint_every: int = 10,
    ) -> PokemonAgentState:
        state = initial_state(
            objective=objective,
            max_steps=max_steps,
            checkpoint_every=checkpoint_every,
        )

        while not state.get("done", False):
            state.update(self._observe(state))
            state.update({"mode": classify_mode(state["observation"])})
            state.update(self._plan(state))
            state.update(self._act(state))
            state.update(self._verify(state))
            if state.get("stuck_score", 0) >= 3 and not state.get("done", False):
                state.update(self._recover(state))
            state.update(self._checkpoint(state))

        return state

    def _observe(self, state: PokemonAgentState) -> PokemonAgentState:
        return {
            "previous_observation": state.get("observation", {}),
            "observation": self.client.observe(),
        }

    def _plan(self, state: PokemonAgentState) -> PokemonAgentState:
        if self.action_planner is None or self.idle_pump is None:
            return plan_next_action(state, self.action_planner)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.action_planner.plan, dict(state))
                while not future.done():
                    self.idle_pump()
                    time.sleep(max(0.001, self.idle_pump_interval))

                planned_action = sanitize_planned_action(future.result())
                if planned_action is not None:
                    planned_action.setdefault("source", "adk")
                    return {"planned_action": planned_action, "plan_error": None}
        except Exception as exc:
            fallback = rule_based_plan(state)
            fallback["plan_error"] = f"{type(exc).__name__}: {exc}"
            return fallback

        fallback = rule_based_plan(state)
        fallback["plan_error"] = None
        return fallback

    def _act(self, state: PokemonAgentState) -> PokemonAgentState:
        action = state.get("planned_action", {"type": "step_frames", "frames": 1})
        action_type = action.get("type")

        if action_type == "press_button":
            result = self.client.press_button(
                button=str(action.get("button", "a")),
                frames=int(action.get("frames", 4)),
                after_frames=int(action.get("after_frames", 8)),
            )
        elif action_type == "execute_actions":
            result = self.client.execute_actions(list(action.get("actions", [])))
        elif action_type == "move_to_screen_tile":
            result = self.client.move_to_screen_tile(
                target_x=int(action["target_x"]),
                target_y=int(action["target_y"]),
                max_steps=int(action.get("max_steps", 1)),
                accept_nearest=bool(action.get("accept_nearest", True)),
            )
        else:
            result = self.client.step_frames(frames=int(action.get("frames", 1)), render=False)

        history = list(state.get("action_history", []))
        history.append({"step": state.get("step_count", 0), "action": action, "result": compact_result(result)})
        return {
            "action_result": result,
            "action_history": history[-100:],
            "step_count": state.get("step_count", 0) + 1,
        }

    def _verify(self, state: PokemonAgentState) -> PokemonAgentState:
        previous = state.get("observation", {})
        result = state.get("action_result", {})
        after = result.get("after_observation", previous)
        before_position = position_tuple(previous)
        after_position = position_tuple(after)
        stuck_score = state.get("stuck_score", 0)
        action = state.get("planned_action", {})

        if action.get("type") == "move_to_screen_tile":
            if result.get("stop_reason") in {"no_path", "max_steps_reached"}:
                stuck_score += 1
            elif before_position is not None and before_position == after_position and result.get("steps_taken", 0) > 0:
                stuck_score += 1
            else:
                stuck_score = max(0, stuck_score - 1)
        else:
            stuck_score = max(0, stuck_score - 1)

        done = state.get("step_count", 0) >= state.get("max_steps", 20)
        return {"observation": after, "stuck_score": stuck_score, "done": done}

    def _recover(self, state: PokemonAgentState) -> PokemonAgentState:
        try:
            result = self.client.load_state(kind="last")
        except Exception:
            result = self.client.reset_to_fixed()
        history = list(state.get("action_history", []))
        history.append({"step": state.get("step_count", 0), "action": {"type": "recover"}, "result": compact_result(result)})
        return {"stuck_score": 0, "action_history": history[-100:], "action_result": result}

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


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    for key in ("before_observation", "after_observation"):
        observation = compact.get(key)
        if isinstance(observation, dict):
            compact[key] = {
                "state": observation.get("state"),
                "tool_step_index": observation.get("tool_step_index"),
                "frame_index": observation.get("frame_index"),
            }
    return compact
