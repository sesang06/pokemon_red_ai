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


def action_plan(
    action: dict,
    *,
    repeat_until: dict | None = None,
    max_repeats: int = 1,
) -> dict:
    return {
        "action": action,
        "repeat_until": repeat_until,
        "max_repeats": max_repeats,
        "reason": f"test action {action.get('type')}",
    }


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


def test_adk_safe_loop_runs_with_fake_client(tmp_path: Path) -> None:
    result = PokemonAdkLoop(
        FakeClient(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=2)

    assert result["done"] is True
    assert result["termination_reason"] == "max_steps_reached"
    assert result["step_count"] == 2
    assert result["action_history"]


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

    assert result["active_action_plan"]["source"] == "rule"
    assert result["plan_error"] == "invalid_json_response (finish_reason=MAX_TOKENS, chars=221)"


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
        action_logger=logger,
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1, checkpoint_every=0)

    log_path = tmp_path / "actions" / "20260814" / "actions.jsonl"
    log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())

    assert result["execution_report"]["action_log_path"] == str(log_path)
    assert log_entry["timestamp"] == "2026-08-14T12:30:00.000"
    assert log_entry["agent"] == "pokemon_red_execution_agent"
    assert log_entry["action"]["type"] in {"move", "buttons"}
    assert log_entry["result"]["stop_reason"]


def test_adk_loop_emits_agent_trace_events(tmp_path: Path) -> None:
    events = []

    class FakePlanner:
        def plan(self, state):
            decision = action_plan({"type": "buttons", "buttons": ["a"], "reason": "advance_dialog"})
            decision.update({
                "thought_summary": "Dialog is open, so advance it with A.",
                "screen_description": "The screen shows a dialog box over the current map.",
                "current_location": "Pallet Town at world position {'x': 5, 'y': 6}.",
                "current_goal": "Advance the visible dialog safely.",
                "future_objective": "Return to overworld exploration after the dialog changes or closes.",
                "decision_rationale": (
                    "The observation indicates dialog mode, so movement would be unsafe and waiting would not actively "
                    "advance the text. Pressing A once is bounded, reversible in the sense that it advances only one "
                    "dialog step, and the next observation can verify whether the dialog changed or closed."
                ),
                "session_dialog": (
                    "Screen: a dialog is visible. Location: Pallet Town around position 5,6. Current goal: advance "
                    "the dialog. Future objective: resume safe exploration after the dialog resolves. Rationale: A "
                    "is the safest bounded action for dialog mode."
                ),
                "decision_trace": {
                    "observations_considered": ["mode=dialog"],
                    "candidate_actions": ["buttons([a])", "buttons([wait])"],
                    "risk_check": "A is bounded.",
                    "decision_basis": "Dialog needs A.",
                },
                "expected_result": "dialog advances",
            })
            return decision

    result = PokemonAdkLoop(
        FakeClient(),
        action_planner=FakePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
        trace=events.append,
    ).run(max_steps=1)

    assert result["plan_decision"]["thought_summary"] == "Dialog is open, so advance it with A."
    assert result["plan_decision"]["screen_description"].startswith("The screen shows")
    assert result["plan_decision"]["decision_rationale"].startswith("The observation indicates")
    planning_dialog = next(entry for entry in result["session_dialog"] if entry["phase"] == "planning_dialog")
    assert planning_dialog["content"].startswith("Screen: a dialog is visible")
    assert result["plan_decision"]["decision_trace"]["decision_basis"] == "Dialog needs A."
    assert [event["phase"] for event in events] == [
        "planning_done",
        "execution_done",
        "result_interpretation",
    ]
    assert "<thought_summary>Dialog is open" in format_trace_event(events[0])
    assert "<session_dialog>Screen: a dialog is visible" in format_trace_event(events[0])
    assert "<decision_trace>" in format_trace_event(events[0])


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


def test_adk_loop_reuses_action_until_condition_and_writes_memory(tmp_path) -> None:
    class DialogClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.dialog_open = True
            self.button_calls = 0

        def press_buttons(self, buttons: list[str]):
            self.button_calls += 1
            if self.button_calls >= 3:
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

    class RepeatPlanner:
        def __init__(self):
            self.calls = 0

        def plan(self, state):
            self.calls += 1
            return action_plan(
                {"type": "buttons", "buttons": ["a", "wait"], "reason": "advance_dialog"},
                repeat_until={"path": "dialog_open", "equals": False},
                max_repeats=4,
            )

    class FakeSummarizer:
        def __init__(self):
            self.payloads = []

        def summarize(self, payload):
            self.payloads.append(payload)
            return {
                "summary": "Reached a useful frontier.",
                "action_succeeded": True,
                "goal_progress": "Reached a useful frontier.",
                "goal_completed": False,
                "verified_state_change": "position changed",
                "failure_reason": "",
                "memory_writes": [
                    {"key": "map:Pallet Town", "value": "Reached a useful frontier."},
                    {"key": "goal:main", "value": "Keep exploring safely."},
                ],
            }

    summarizer = FakeSummarizer()
    planner = RepeatPlanner()
    client = DialogClient()
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")

    result = PokemonAdkLoop(
        client,
        action_planner=planner,
        result_interpreter=summarizer,
        memory_store=store,
    ).run(max_steps=3, checkpoint_every=0)

    assert result["done"] is True
    assert planner.calls == 1
    assert client.button_calls == 3
    assert result["action_outcome"]["status"] == "condition_met"
    assert result["active_action_plan"]["repeat_count"] == 3
    assert result["interpretation"]["action_succeeded"] is True
    assert result["interpretation"]["goal_progress"] == "Reached a useful frontier."
    assert result["interpretation"]["goal_completed"] is False
    assert result["interpretation"]["verified_state_change"] == "position changed"
    assert result["interpretation"]["failure_reason"] == ""
    assert result["interpretation"]["memory_written"] == ["map:Pallet Town", "goal:main"]
    assert store.get("map:Pallet Town")["value"] == "Reached a useful frontier."
    assert len(summarizer.payloads) == 1
    assert summarizer.payloads[0]["last_result"]["status"] == "condition_met"


def test_adk_loop_compacts_action_history_after_twenty_turns(tmp_path) -> None:
    result = PokemonAdkLoop(
        FakeClient(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=22, checkpoint_every=0)

    assert len(result["action_history"]) == 20
    assert len(result["session_dialog"]) == 20
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
