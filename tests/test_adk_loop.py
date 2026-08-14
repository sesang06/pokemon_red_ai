from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from pokemon_agent.adk_agent.agents.executor.agent import ExecutionAgent
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.runtime.logging import DateGroupedActionLogger
from pokemon_agent.adk_agent.runtime.trace import format_trace_event
from pokemon_agent.memory.file_memory import FileLongTermMemory


def action_plan(action: dict) -> dict:
    return {
        "action": action,
        "reason": f"test action {action.get('type')}",
    }


class WaitPlanner:
    def plan(self, state):
        return action_plan({"type": "buttons", "buttons": ["wait"], "reason": "observe_once"})


class FakeClient:
    def __init__(self):
        self.x = 5
        self.y = 6
        self.saved = 0
        self.loaded = 0
        self.world_move_calls: list[dict[str, int]] = []

    def observe(self):
        return self._observation()

    def press_buttons(self, buttons: list[str]):
        return {"executed_actions": [{"button": button} for button in buttons], "after_observation": self._observation()}

    def wait(self):
        return {"waited": True, "stop_reason": "wait_complete", "after_observation": self._observation()}

    def save_state(self, kind: str = "snapshot", path: str | None = None):
        self.saved += 1
        return {"path": "states/last.state"}

    def load_state(self, kind: str = "fixed", path: str | None = None):
        self.loaded += 1
        return {"after_observation": self._observation()}

    def reset_to_fixed(self):
        self.loaded += 1
        return {"after_observation": self._observation()}

    def move_to_world_cell(self, target_x: int, target_y: int):
        self.world_move_calls.append({"target_x": target_x, "target_y": target_y})
        self.x = target_x
        self.y = target_y
        return {
            "stop_reason": "target_reached",
            "steps_taken": 1,
            "requested_world_cell": {"x": target_x, "y": target_y},
            "resolved_world_cell": {"x": target_x, "y": target_y},
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
            "walk_area_collision": [[1 for _ in range(10)] for _ in range(9)],
        }


def test_adk_loop_without_llm_planner_stops_without_game_input(tmp_path: Path) -> None:
    client = FakeClient()
    result = PokemonAdkLoop(
        client,
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=2)

    assert result["done"] is True
    assert result["termination_reason"] == "planning_failed"
    assert result["plan_error"] == "planner_unavailable"
    assert result["step_count"] == 0
    assert result["action_history"] == []
    assert client.world_move_calls == []


def test_adk_loop_uses_action_planner_when_available(tmp_path: Path) -> None:
    class FakePlanner:
        def plan(self, state):
            return action_plan({"type": "buttons", "buttons": ["wait"], "reason": "observe_once"})

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=FakePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1)

    assert result["planned_action"]["source"] == "adk"
    assert result["planned_action"]["buttons"] == ["wait"]
    assert result["active_action_plan"]["action"]["reason"] == "observe_once"
    assert result["plan_decision"]["agent"] == "pokemon_red_planning_agent"
    assert result["execution_report"]["agent"] == "pokemon_red_execution_agent"


def test_adk_loop_exposes_empty_llm_decision_as_plan_error(tmp_path: Path) -> None:
    class TruncatedPlanner:
        last_plan_error = "invalid_json_response (finish_reason=MAX_TOKENS, chars=221)"

        def plan(self, state):
            return None

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=TruncatedPlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1)

    assert result["active_action_plan"] == {}
    assert result["plan_error"] == "invalid_json_response (finish_reason=MAX_TOKENS, chars=221)"
    assert result["termination_reason"] == "planning_failed"
    assert result["step_count"] == 0


def test_execution_agent_allows_multi_step_world_moves(tmp_path: Path) -> None:
    class MovePlanner:
        def plan(self, state):
            return action_plan(
                {"type": "move", "target": [10, 6], "reason": "move_to_target"},
            )

    client = FakeClient()
    result = PokemonAdkLoop(
        client,
        action_planner=MovePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1)

    assert result["planned_action"]["target"] == [10, 6]
    assert client.world_move_calls[-1] == {"target_x": 10, "target_y": 6}


def test_execution_agent_records_move_errors_without_crashing(tmp_path: Path) -> None:
    class ErrorClient(FakeClient):
        def move_to_world_cell(self, target_x: int, target_y: int):
            raise ValueError("target world cell is outside the current visible walk area")

    class MovePlanner:
        def plan(self, state):
            return action_plan(
                {"type": "move", "target": [3, 5], "reason": "move_to_stale_target"},
            )

    result = PokemonAdkLoop(
        ErrorClient(),
        action_planner=MovePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=2, checkpoint_every=0)

    assert result["done"] is True
    assert result["step_count"] == 2
    assert result["stuck_score"] == 2
    assert result["action_history"][-1]["result"]["stop_reason"] == "execution_error"
    assert "ValueError" in result["action_history"][-1]["result"]["error"]


def test_execution_agent_writes_date_grouped_action_log(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 14, 12, 30, 0)
    logger = DateGroupedActionLogger(tmp_path / "actions", clock=lambda: fixed_now)
    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=WaitPlanner(),
        action_logger=logger,
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1, checkpoint_every=0)

    log_path = tmp_path / "actions" / "20260814" / "actions.jsonl"
    log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())

    assert result["execution_report"]["action_log_path"] == str(log_path)
    assert log_entry["timestamp"] == "2026-08-14T12:30:00.000"
    assert log_entry["agent"] == "pokemon_red_execution_agent"
    assert log_entry["action"]["type"] in {"move", "buttons"}
    assert log_entry["result"]["executed_actions"] == [{"button": "wait"}]


def test_adk_loop_emits_agent_trace_events(tmp_path: Path) -> None:
    events = []

    class FakePlanner:
        thinking_summary_callback = None
        last_thinking_summary = "Dialog is open, so one A press is the bounded next action."

        def plan(self, state):
            self.thinking_summary_callback(self.last_thinking_summary)
            return {
                "screen_description": "오박사 연구실의 대화 화면",
                "current_location": "Oak's Lab (5, 6)",
                "thought_summary": "대화를 진행한 뒤 새 상태를 확인합니다.",
                "action": {"type": "buttons", "buttons": ["a"], "reason": "advance_dialog"},
            }

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=FakePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
        trace=events.append,
    ).run(max_steps=1)

    assert set(result["plan_decision"]) == {
        "agent",
        "phase",
        "objective",
        "current_goal",
        "action_plan",
        "memory_keys_read",
        "reason",
        "screen_description",
        "current_location",
        "thought_summary",
    }
    assert result["plan_decision"]["reason"] == "advance_dialog"
    assert result["plan_decision"]["screen_description"] == "오박사 연구실의 대화 화면"
    assert result["plan_decision"]["current_location"] == "Oak's Lab (5, 6)"
    assert result["plan_decision"]["thought_summary"] == "대화를 진행한 뒤 새 상태를 확인합니다."
    assert "session_dialog" not in result
    assert [event["phase"] for event in events] == [
        "planning_thinking",
        "planning_done",
        "execution_done",
        "result_interpretation",
    ]
    assert events[0]["thinking_summary"].startswith("Dialog is open")
    assert events[1]["thinking_summary"].startswith("Dialog is open")
    assert events[1]["screen_description"] == "오박사 연구실의 대화 화면"
    assert format_trace_event(events[1]).startswith(
        "[agent-trace] pokemon_red_planning_agent phase=planning_done"
    )
    assert "thought_summary: 대화를 진행한 뒤 새 상태를 확인합니다." in format_trace_event(events[1])


def test_adk_loop_pumps_realtime_while_waiting_for_planner(tmp_path: Path) -> None:
    pump_calls = 0

    class SlowPlanner:
        def plan(self, state):
            time.sleep(0.05)
            return action_plan({"type": "buttons", "buttons": ["wait"], "reason": "wait_once"})

    def pump():
        nonlocal pump_calls
        pump_calls += 1

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=SlowPlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
        idle_pump=pump,
        idle_pump_interval=0.005,
    ).run(max_steps=1)

    assert result["done"] is True
    assert pump_calls > 0


def test_adk_loop_does_not_recover_when_stuck_score_reaches_threshold(tmp_path: Path) -> None:
    class StuckClient(FakeClient):
        def move_to_world_cell(self, target_x: int, target_y: int):
            return {
                "stop_reason": "no_path",
                "steps_taken": 0,
                "requested_world_cell": {"x": target_x, "y": target_y},
                "executed_actions": [],
                "after_observation": self._observation(),
            }

    class StuckPlanner:
        def plan(self, state):
            return action_plan(
                {"type": "move", "target": [6, 6], "reason": "move_into_blocked_path"},
            )

    client = StuckClient()

    result = PokemonAdkLoop(
        client,
        action_planner=StuckPlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=4, checkpoint_every=0)

    assert result["done"] is True
    assert result["step_count"] == 4
    assert result["stuck_score"] == 4
    assert client.loaded == 0
    assert all(entry["action"]["type"] != "recover" for entry in result["action_history"])


def test_adk_loop_executes_each_button_sequence_once_and_replans_each_cycle(tmp_path) -> None:
    class DialogClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.dialog_open = True
            self.button_calls = 0
            self.received_buttons = []

        def press_buttons(self, buttons: list[str]):
            self.button_calls += 1
            self.received_buttons.append(list(buttons))
            if buttons.count("a") >= 3:
                self.dialog_open = False
            return {
                "stop_reason": "buttons_complete",
                "executed_actions": [{"button": button} for button in buttons],
                "after_observation": self._observation(),
            }

        def _observation(self):
            observation = super()._observation()
            observation["state"]["dialog_open"] = self.dialog_open
            observation["state"]["mode"] = "talk" if self.dialog_open else "explore"
            return observation

    class SequencePlanner:
        def __init__(self):
            self.calls = 0

        def plan(self, state):
            self.calls += 1
            return action_plan(
                {
                    "type": "buttons",
                    "buttons": ["a", "wait", "a", "wait", "a"],
                    "reason": "advance_dialog_three_times",
                }
            )

    planner = SequencePlanner()
    client = DialogClient()
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    result = PokemonAdkLoop(
        client,
        action_planner=planner,
        memory_store=store,
    ).run(max_steps=3, checkpoint_every=0)

    assert result["done"] is True
    assert planner.calls == 3
    assert client.button_calls == 3
    assert client.received_buttons == [["a", "wait", "a", "wait", "a"]] * 3
    assert result["action_outcome"]["status"] == "single_action_complete"
    assert not {"repeat_until", "repeat_count", "max_repeats"}.intersection(result["active_action_plan"])


def test_adk_loop_compacts_action_history_after_twenty_turns(tmp_path) -> None:
    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=WaitPlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=22, checkpoint_every=0)

    assert len(result["action_history"]) == 20
    assert result["history_summary"].startswith("Compacted 1 state transition(s)")


def test_execution_agent_rejects_unknown_action_types() -> None:
    state = {
        "planned_action": {"type": "unsupported_action"},
        "action_history": [],
        "step_count": 0,
    }

    result = ExecutionAgent(FakeClient()).execute(state)

    assert result["planned_action"]["type"] == "buttons"
    assert result["planned_action"]["buttons"] == ["wait"]
    assert result["execution_report"]["success_hint"] == "realtime_wait_complete"
