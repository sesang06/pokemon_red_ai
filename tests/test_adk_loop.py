from __future__ import annotations

import time

from pokemon_agent.adk_agent.loop import PokemonAdkLoop


class FakeClient:
    def __init__(self):
        self.x = 5
        self.y = 6
        self.saved = 0
        self.loaded = 0

    def observe(self):
        return self._observation()

    def press_button(self, button: str, frames: int = 4, after_frames: int = 8):
        return {"executed_actions": [{"button": button}], "after_observation": self._observation()}

    def execute_actions(self, actions):
        return {"executed_actions": actions, "after_observation": self._observation()}

    def step_frames(self, frames: int = 1, render: bool = False):
        return {"frames": frames, "after_observation": self._observation()}

    def save_state(self, kind: str = "snapshot", path: str | None = None):
        self.saved += 1
        return {"path": "states/last.state"}

    def load_state(self, kind: str = "fixed", path: str | None = None):
        self.loaded += 1
        return {"after_observation": self._observation()}

    def reset_to_fixed(self):
        self.loaded += 1
        return {"after_observation": self._observation()}

    def move_to_screen_tile(self, target_x: int, target_y: int, max_steps: int = 8, accept_nearest: bool = True):
        self.x += 1
        return {
            "stop_reason": "target_reached",
            "steps_taken": 1,
            "executed_actions": [{"button": "right"}],
            "after_observation": self._observation(),
        }

    def pump_realtime(self):
        return {"frames_ticked": 1}

    def _observation(self):
        return {
            "state": {
                "mode": "explore",
                "in_battle": False,
                "dialog_open": False,
                "position": {"x": self.x, "y": self.y},
            },
            "game_area_collision": [[1 for _ in range(20)] for _ in range(18)],
        }


def test_adk_safe_loop_runs_with_fake_client() -> None:
    result = PokemonAdkLoop(FakeClient()).run(max_steps=2)

    assert result["done"] is True
    assert result["step_count"] == 2
    assert result["action_history"]


def test_adk_loop_uses_action_planner_when_available() -> None:
    class FakePlanner:
        def plan(self, state):
            return {
                "type": "press_button",
                "button": "a",
                "frames": 4,
                "after_frames": 8,
                "reason": "adk says advance",
            }

    result = PokemonAdkLoop(FakeClient(), action_planner=FakePlanner()).run(max_steps=1)

    assert result["planned_action"]["source"] == "adk"
    assert result["planned_action"]["button"] == "a"


def test_adk_loop_pumps_realtime_while_waiting_for_planner() -> None:
    pump_calls = 0

    class SlowPlanner:
        def plan(self, state):
            time.sleep(0.05)
            return {"type": "step_frames", "frames": 1, "reason": "waited"}

    def pump():
        nonlocal pump_calls
        pump_calls += 1

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=SlowPlanner(),
        idle_pump=pump,
        idle_pump_interval=0.005,
    ).run(max_steps=1)

    assert result["done"] is True
    assert pump_calls > 0
