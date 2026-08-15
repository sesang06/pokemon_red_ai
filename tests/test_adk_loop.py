from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from pydantic import Field

from pokemon_agent.adk_agent.agents.interpreter.agent import GoogleAdkResultInterpreter
from pokemon_agent.adk_agent.agents.planner.agent import GoogleAdkPlanner
from pokemon_agent.adk_agent.agents.executor.agent import ExecutionAgent
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.coordinator.workflow_agent import (
    AUTOPLAY_SESSION_ID,
    PokemonExecutionAgent,
    PokemonRedTeamAgent,
    _interpreter_content,
    _requested_steps,
    run_traced_pokemon_loop,
)
from google.genai import types
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
        self.saved_kinds: list[str] = []
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
        self.saved_kinds.append(kind)
        return {"path": f"states/{'fixed_start' if kind == 'fixed' else kind}.state"}

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
            "screenshot": {
                "format": "png",
                "width": 160,
                "height": 144,
                "base64": base64.b64encode(
                    f"screen-{self.x}-{self.y}".encode("ascii")
                ).decode("ascii"),
            },
        }


class StaticResponseLlm(BaseLlm):
    response_text: str
    requests: list[object] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request.model_copy(deep=True))
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=self.response_text)],
            ),
            partial=False,
            turnComplete=True,
        )


class ImportantEventClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.party: list[dict] = []

    def press_buttons(self, buttons: list[str]):
        self.party = [{"species": "BULBASAUR", "level": 5}]
        return {
            "executed_actions": [{"button": button} for button in buttons],
            "stop_reason": "buttons_complete",
            "after_observation": self._observation(),
        }

    def _observation(self):
        observation = super()._observation()
        observation["state"]["party"] = list(self.party)
        return observation


def native_wait_loop(tmp_path: Path, *, client=None):
    memory = FileLongTermMemory(tmp_path / "memory.json")
    planner = GoogleAdkPlanner(model="fake-planner", memory_store=memory)
    interpreter = GoogleAdkResultInterpreter(model="fake-interpreter", memory_store=memory)
    planner.agent.model = StaticResponseLlm(
        model="fake-planner",
        response_text=(
            '{"screen_description":"Oak Lab","current_location":"Oak Lab (5,6)",'
            '"thought_summary":"Wait once and observe the current state.",'
            '"action":{"type":"buttons","buttons":["wait"],"reason":"observe_once"}}'
        ),
    )
    interpreter.agent.model = StaticResponseLlm(
        model="fake-interpreter",
        response_text=(
            '{"screen_description":"Oak Lab","current_location":"Oak Lab (5,6)",'
            '"thought_summary":"The action completed without a durable event.",'
            '"summary":"No durable event.","memory_saved":false}'
        ),
    )
    return PokemonAdkLoop(
        client or FakeClient(),
        action_planner=planner,
        result_interpreter=interpreter,
        memory_store=memory,
    )


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

    assert result["active_action_plan"]["action"]["source"] == "adk"
    assert result["active_action_plan"]["action"]["buttons"] == ["wait"]
    assert result["active_action_plan"]["action"]["reason"] == "observe_once"
    assert result["plan_decision"]["agent"] == "pokemon_red_planning_agent"
    assert result["execution_report"]["agent"] == "pokemon_red_execution_agent"


def test_execution_uses_active_action_plan_as_the_single_action_source() -> None:
    class RecordingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.received_buttons: list[list[str]] = []

        def press_buttons(self, buttons: list[str]):
            self.received_buttons.append(list(buttons))
            return super().press_buttons(buttons)

    client = RecordingClient()
    result = ExecutionAgent(client).execute(
        {
            "active_action_plan": {
                "action": {"type": "buttons", "buttons": ["a"], "reason": "advance_dialog"},
                "status": "active",
            },
            "observation": client.observe(),
        }
    )

    assert client.received_buttons == [["a"]]
    assert result["active_action_plan"]["action"] == {
        "type": "buttons",
        "buttons": ["a"],
        "reason": "advance_dialog",
    }


def test_custom_adk_team_traces_all_three_runtime_phases(tmp_path: Path) -> None:
    loop = native_wait_loop(tmp_path)
    team = PokemonRedTeamAgent(loop=loop, max_steps=1, checkpoint_every=0)

    assert AUTOPLAY_SESSION_ID == "pokemon-red-autoplay"
    assert [agent.name for agent in team.sub_agents] == [
        "pokemon_red_planning_agent",
        "pokemon_red_execution_agent",
        "pokemon_red_result_interpreter_agent",
    ]
    assert all(agent.parent_agent is team for agent in team.sub_agents)

    loop = native_wait_loop(tmp_path / "run")
    events = []
    result = run_traced_pokemon_loop(
        loop,
        max_steps=1,
        checkpoint_every=0,
        session_db_path=str(tmp_path / "adk_sessions.db"),
        event_sink=events.append,
    )
    authors = [event.author for event in events]

    assert result["done"] is True
    assert result["step_count"] == 1
    assert result["termination_reason"] == "max_steps_reached"
    assert "pokemon_red_team" in authors
    assert "pokemon_red_planning_agent" in authors
    assert "pokemon_red_execution_agent" in authors
    assert "pokemon_red_result_interpreter_agent" in authors
    output_events = [event for event in events if event.output is not None]
    assert len(output_events) == 1
    assert output_events[0].output["phase"] == "completed"


def test_team_executes_against_its_own_state_without_child_parent_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def forbidden_child_run(*_args, **_kwargs):
        raise AssertionError("execution child must not resolve state through parent_agent")
        yield

    monkeypatch.setattr(PokemonExecutionAgent, "run_async", forbidden_child_run)
    client = FakeClient()

    result = run_traced_pokemon_loop(
        native_wait_loop(tmp_path, client=client),
        max_steps=1,
        checkpoint_every=0,
        session_db_path=str(tmp_path / "adk_sessions.db"),
    )

    assert result["active_action_plan"]["action"]["buttons"] == ["wait"]
    assert result["active_action_plan"]["action"]["reason"] == "observe_once"
    assert result["execution_report"]["action"] == result["active_action_plan"]["action"]


def test_custom_adk_team_allows_multiple_steps_with_one_node_output(tmp_path: Path) -> None:
    loop = native_wait_loop(tmp_path)
    interpreter_model = loop.result_interpreter_agent.summarizer.agent.model
    events = []

    result = run_traced_pokemon_loop(
        loop,
        max_steps=3,
        checkpoint_every=0,
        session_db_path=str(tmp_path / "adk_sessions.db"),
        event_sink=events.append,
    )

    assert result["step_count"] == 3
    assert result["termination_reason"] == "max_steps_reached"
    output_events = [event for event in events if event.output is not None]
    assert len(output_events) == 1
    assert output_events[0].output == {
        "phase": "completed",
        "step": 3,
        "done": True,
        "termination_reason": "max_steps_reached",
        "goal": {"main": "Complete Pokemon Red", "sub": ""},
    }
    assert len(interpreter_model.requests) == 3
    for request in interpreter_model.requests:
        media_parts = [
            part
            for content in request.contents
            for part in (content.parts or [])
            if getattr(part, "inline_data", None)
        ]
        assert len(media_parts) == 1


def test_interpreter_content_contains_planner_conclusion_and_only_latest_screenshot() -> None:
    current_screen = base64.b64encode(b"current-screen").decode("ascii")
    previous_screen = base64.b64encode(b"previous-screen").decode("ascii")
    content = _interpreter_content(
        {
            "step_count": 4,
            "observation": {
                "state": {
                    "map_name": "Route 1",
                    "position": {"x": 8, "y": 28},
                    "mode": "battle",
                    "in_battle": True,
                    "dialog_open": True,
                },
                "screenshot": {"base64": current_screen},
            },
            "previous_observation": {"screenshot": {"base64": previous_screen}},
            "plan_decision": {
                "screen_description": "A wild Rattata battle is visible.",
                "current_location": "Route 1 at coordinates [8, 28]",
                "thought_summary": "Advance the battle narrative once.",
            },
            "active_action_plan": {
                "action": {
                    "type": "buttons",
                    "buttons": ["a"],
                    "reason": "advance_battle_narrative_continuation",
                },
                "status": "single_action_complete",
            },
        }
    )

    payload = json.loads(content.parts[0].text)
    assert payload["planner_conclusion"] == {
        "screen_description": "A wild Rattata battle is visible.",
        "current_location": "Route 1 at coordinates [8, 28]",
        "thought_summary": "Advance the battle narrative once.",
        "action": {
            "type": "buttons",
            "buttons": ["a"],
            "reason": "advance_battle_narrative_continuation",
        },
    }
    media_parts = [part for part in content.parts if getattr(part, "inline_data", None)]
    assert len(media_parts) == 1
    assert media_parts[0].inline_data.data == b"current-screen"
    assert (
        media_parts[0].media_resolution.level
        == types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM
    )


def test_native_adk_children_emit_model_events_in_the_outer_team_trace(tmp_path: Path) -> None:
    memory = FileLongTermMemory(tmp_path / "memory.json")
    planner = GoogleAdkPlanner(model="fake-planner", memory_store=memory)
    interpreter = GoogleAdkResultInterpreter(model="fake-interpreter", memory_store=memory)
    planner_model = StaticResponseLlm(
        model="fake-planner",
        response_text=(
            '{"screen_description":"Oak Lab","current_location":"Oak Lab (5,6)",'
            '"thought_summary":"Interact once and verify the result.",'
            '"action":{"type":"buttons","buttons":["a"],"reason":"interact"}}'
        ),
    )
    interpreter_model = StaticResponseLlm(
        model="fake-interpreter",
        response_text=(
            '{"screen_description":"A Pokemon joined the party.",'
            '"current_location":"Oak Lab (5,6)",'
            '"thought_summary":"The party change verifies acquisition.",'
            '"summary":"Bulbasaur was acquired.","memory_saved":false}'
        ),
    )
    planner.agent.model = planner_model
    interpreter.agent.model = interpreter_model
    loop = PokemonAdkLoop(
        ImportantEventClient(),
        action_planner=planner,
        result_interpreter=interpreter,
        memory_store=memory,
    )
    events = []

    result = run_traced_pokemon_loop(
        loop,
        max_steps=1,
        checkpoint_every=0,
        session_db_path=str(tmp_path / "adk_sessions.db"),
        event_sink=events.append,
    )

    authors = [event.author for event in events]
    assert result["step_count"] == 1
    assert result["interpretation"]["llm_called"] is True
    assert "pokemon_red_planning_agent" in authors
    assert "pokemon_red_result_interpreter_agent" in authors
    assert len(planner_model.requests) == 1
    assert len(interpreter_model.requests) == 1
    planner_payloads = [
        part.text
        for content in planner_model.requests[0].contents
        for part in (content.parts or [])
        if part.text and '"state"' in part.text
    ]
    assert planner_payloads
    assert '"position":{"x":5,"y":6}' in planner_payloads[-1]


def test_custom_adk_team_reads_requested_step_count_from_user_message() -> None:
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text="포켓몬을 100스텝 플레이해줘")],
    )

    assert _requested_steps(content) == 100
    assert _requested_steps(None) == 100


def test_adk_loop_saves_fixed_state_after_every_completed_turn(tmp_path: Path) -> None:
    client = FakeClient()

    result = PokemonAdkLoop(
        client,
        action_planner=WaitPlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=3, checkpoint_every=2)

    assert client.saved_kinds == ["fixed", "fixed", "last", "fixed"]
    assert result["fixed_state_path"] == "states/fixed_start.state"
    assert result["checkpoint_path"] == "states/last.state"


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

    assert result["active_action_plan"]["action"]["target"] == [10, 6]
    assert client.world_move_calls[-1] == {"target_x": 10, "target_y": 6}


def test_execution_agent_visits_waypoints_before_final_world_target(tmp_path: Path) -> None:
    class MovePlanner:
        def plan(self, state):
            return action_plan(
                {
                    "type": "move",
                    "waypoints": [[6, 6], [8, 6]],
                    "target": [10, 6],
                    "reason": "follow_known_route",
                },
            )

    client = FakeClient()
    result = PokemonAdkLoop(
        client,
        action_planner=MovePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1, checkpoint_every=0)

    execution = result["action_result"]
    assert client.world_move_calls == [
        {"target_x": 6, "target_y": 6},
        {"target_x": 8, "target_y": 6},
        {"target_x": 10, "target_y": 6},
    ]
    assert execution["completed_waypoints"] == 2
    assert execution["final_target_attempted"] is True
    assert execution["final_target_reached"] is True
    assert [entry["kind"] for entry in execution["route_results"]] == [
        "waypoint",
        "waypoint",
        "target",
    ]
    assert execution["steps_taken"] == 3


def test_execution_agent_stops_route_at_failed_waypoint(tmp_path: Path) -> None:
    class BlockedWaypointClient(FakeClient):
        def move_to_world_cell(self, target_x: int, target_y: int):
            if (target_x, target_y) == (8, 6):
                self.world_move_calls.append({"target_x": target_x, "target_y": target_y})
                return {
                    "stop_reason": "movement_blocked",
                    "steps_taken": 0,
                    "requested_world_cell": {"x": target_x, "y": target_y},
                    "executed_actions": [],
                    "after_observation": self._observation(),
                }
            return super().move_to_world_cell(target_x, target_y)

    class MovePlanner:
        def plan(self, state):
            return action_plan(
                {
                    "type": "move",
                    "waypoints": [[6, 6], [8, 6]],
                    "target": [10, 6],
                    "reason": "follow_blocked_route",
                },
            )

    client = BlockedWaypointClient()
    result = PokemonAdkLoop(
        client,
        action_planner=MovePlanner(),
        memory_store=FileLongTermMemory(tmp_path / "memory.json"),
    ).run(max_steps=1, checkpoint_every=0)

    execution = result["action_result"]
    assert client.world_move_calls == [
        {"target_x": 6, "target_y": 6},
        {"target_x": 8, "target_y": 6},
    ]
    assert execution["completed_waypoints"] == 1
    assert execution["final_target_attempted"] is False
    assert execution["final_target_reached"] is False
    assert execution["stop_reason"] == "movement_blocked"


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
        def plan(self, state):
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
        "goal",
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
        "planning_done",
        "execution_done",
        "result_interpretation",
    ]
    assert "thinking_summary" not in events[0]
    assert events[0]["screen_description"] == "오박사 연구실의 대화 화면"
    assert format_trace_event(events[0]).startswith(
        "[agent-trace] pokemon_red_planning_agent phase=planning_done"
    )
    assert "thought_summary: 대화를 진행한 뒤 새 상태를 확인합니다." in format_trace_event(events[0])


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
        "active_action_plan": {
            "action": {"type": "unsupported_action"},
            "status": "active",
        },
        "action_history": [],
        "step_count": 0,
    }

    result = ExecutionAgent(FakeClient()).execute(state)

    assert result["active_action_plan"]["action"]["type"] == "buttons"
    assert result["active_action_plan"]["action"]["buttons"] == ["wait"]
    assert result["execution_report"]["success_hint"] == "realtime_wait_complete"
