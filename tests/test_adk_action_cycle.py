from __future__ import annotations

from pokemon_agent.adk_agent.coordinator.action_cycle import (
    build_state_diff,
    verify_action_cycle,
)
from pokemon_agent.adk_agent.agents.interpreter.agent import compact_interpreter_context
from pokemon_agent.adk_agent.agents.interpreter.agent import ResultInterpreterAgent


def observation(*, x: int = 5, y: int = 6, dialog_open: bool = True) -> dict:
    return {
        "state": {
            "map_id": 40,
            "map_name": "Oak's Lab",
            "position": {"x": x, "y": y},
            "mode": "talk" if dialog_open else "explore",
            "dialog_open": dialog_open,
            "in_battle": False,
            "flags": {"oak_asked_to_choose_mon": True, "got_starter": False},
            "counts": {"party": 0},
        }
    }


def action_plan() -> dict:
    return {
        "action": {"type": "buttons", "buttons": ["a", "wait"], "reason": "advance_dialog"},
        "status": "active",
    }


def test_action_cycle_completes_after_exactly_one_execution() -> None:
    before = observation(dialog_open=True)
    after = observation(dialog_open=False)
    updated, outcome = verify_action_cycle(
        action_plan(),
        action_result={"stop_reason": "buttons_complete"},
        state_diff=build_state_diff(before, after),
    )

    assert updated["status"] == "single_action_complete"
    assert outcome["status"] == "single_action_complete"
    assert outcome["state_changes"] == ["dialog_closed"]
    assert not {"repeat_until", "repeat_count", "max_repeats", "condition_result"}.intersection(outcome)


def test_action_cycle_stops_on_execution_interruption() -> None:
    current = observation(dialog_open=True)
    state_diff = build_state_diff(current, current)
    _, interrupted = verify_action_cycle(
        action_plan(),
        action_result={"stop_reason": "realtime_ticker_stopped"},
        state_diff=state_diff,
    )

    assert interrupted["status"] == "interrupted"


def test_dialog_or_battle_transition_completes_the_move_action() -> None:
    current = observation(dialog_open=False)
    dialog = observation(dialog_open=True)

    for stop_reason in ("interrupted_dialog", "interrupted_battle", "interrupted_menu"):
        _, outcome = verify_action_cycle(
            action_plan(),
            action_result={"stop_reason": stop_reason},
            state_diff=build_state_diff(current, dialog),
        )

        assert outcome["status"] == "single_action_complete"
        assert outcome["action_result"] == "success"
        assert outcome["reason"] == stop_reason


def test_interpreter_context_contains_action_outcome_without_task_fields() -> None:
    before = observation(dialog_open=True)
    after = observation(dialog_open=False)
    payload = compact_interpreter_context(
        {
            "step_count": 3,
            "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter Pokemon"},
            "observation": after,
            "active_action_plan": {
                **action_plan(),
                "status": "single_action_complete",
            },
            "plan_decision": {
                "screen_description": "Professor Oak's dialog is open.",
                "current_location": "Oak's Lab (5, 6)",
                "thought_summary": "Advance the dialog once and verify the result.",
            },
            "action_outcome": {
                "status": "single_action_complete",
                "reason": "single_action_complete",
            },
            "state_diff": build_state_diff(before, after),
        }
    )

    assert payload["action_plan"]["action"]["type"] == "buttons"
    assert payload["planner_conclusion"] == {
        "screen_description": "Professor Oak's dialog is open.",
        "current_location": "Oak's Lab (5, 6)",
        "thought_summary": "Advance the dialog once and verify the result.",
        "action": action_plan()["action"],
    }
    assert payload["last_result"]["status"] == "single_action_complete"
    assert payload["state_changes"] == ["dialog", "mode"]
    assert "before" not in str(payload)
    assert "after" not in str(payload)
    assert "repeat" not in str(payload)
    assert "task" not in payload


def test_interpreter_context_only_keeps_allowed_battle_opponent_fields() -> None:
    current = observation(dialog_open=False)
    current["state"].update(
        {
            "in_battle": True,
            "mode": "battle",
            "battle": {
                "active": True,
                "opponent": {
                    "species": "Rattata",
                    "level": 4,
                    "hp": 9,
                    "max_hp": 15,
                    "status": "OK",
                    "types": ["Normal"],
                    "moves": ["Tackle"],
                    "move_pp": [34],
                },
            },
        }
    )

    payload = compact_interpreter_context({"observation": current})

    assert payload["state"]["opponent"] == {
        "species": "Rattata",
        "level": 4,
        "hp": 9,
        "max_hp": 15,
        "status": "OK",
        "types": ["Normal"],
    }
    assert "failed_preconditions" not in str(payload)


def test_interpreter_context_includes_compact_dialog_and_party_memory_evidence() -> None:
    current = observation(dialog_open=True)
    current["state"].update(
        {
            "dialog_text": "So! You want BULBASAUR?",
            "counts": {"party": 1},
            "party": [
                {
                    "species": "Bulbasaur",
                    "nickname": "BULBASAUR",
                    "level": 5,
                    "status": "OK",
                    "hp": 19,
                    "max_hp": 19,
                }
            ],
        }
    )

    payload = compact_interpreter_context(
        {
            "step_count": 5,
            "observation": current,
            "active_action_plan": action_plan(),
            "action_outcome": {"status": "single_action_complete"},
            "state_diff": {},
        }
    )

    assert payload["state"]["dialog_text"] == "So! You want BULBASAUR?"
    assert payload["state"]["party"] == [
        {
            "species": "Bulbasaur",
            "nickname": "BULBASAUR",
            "level": 5,
            "status": "OK",
        }
    ]


def test_interpreter_context_keeps_compact_remote_movement_result() -> None:
    payload = compact_interpreter_context(
        {
            "step_count": 4,
            "observation": observation(),
            "active_action_plan": {
                "action": {"type": "move", "target": [20, 5], "reason": "head_east"},
                "status": "active",
            },
            "action_outcome": {
                "status": "single_action_complete",
                "reason": "single_action_complete",
            },
            "execution_report": {
                "stop_reason": "target_reached",
                "result": {
                    "requested_world_cell": {"x": 20, "y": 5},
                    "resolved_world_cell": {"x": 20, "y": 5},
                    "target_out_of_visible_area": True,
                    "requested_target_reached": True,
                    "resolved_target_reached": True,
                    "steps_taken": 15,
                    "navigation_replans": 2,
                    "stop_reason": "target_reached",
                },
            },
        }
    )

    assert payload["last_result"]["movement"] == {
        "requested_target": [20, 5],
        "requested_world_cell": {"x": 20, "y": 5},
        "resolved_world_cell": {"x": 20, "y": 5},
        "target_out_of_visible_area": True,
        "requested_target_reached": True,
        "resolved_target_reached": True,
        "steps_taken": 15,
        "navigation_replans": 2,
        "stop_reason": "target_reached",
    }


def test_interpreter_context_reports_waypoint_route_progress() -> None:
    payload = compact_interpreter_context(
        {
            "step_count": 4,
            "observation": observation(x=8, y=5, dialog_open=False),
            "active_action_plan": {
                "action": {
                    "type": "move",
                    "waypoints": [[6, 5], [8, 5]],
                    "target": [12, 5],
                    "reason": "follow_route",
                },
                "status": "active",
            },
            "action_outcome": {
                "status": "interrupted",
                "reason": "movement_blocked",
            },
            "execution_report": {
                "stop_reason": "movement_blocked",
                "result": {
                    "requested_world_cell": {"x": 8, "y": 5},
                    "requested_final_world_cell": {"x": 12, "y": 5},
                    "requested_waypoints": [[6, 5], [8, 5]],
                    "completed_waypoints": 1,
                    "final_target_attempted": False,
                    "final_target_reached": False,
                    "route_results": [
                        {
                            "index": 0,
                            "kind": "waypoint",
                            "target": [6, 5],
                            "reached": True,
                            "stop_reason": "target_reached",
                        },
                        {
                            "index": 1,
                            "kind": "waypoint",
                            "target": [8, 5],
                            "reached": False,
                            "stop_reason": "movement_blocked",
                        },
                    ],
                    "steps_taken": 1,
                    "stop_reason": "movement_blocked",
                },
            },
        }
    )

    movement = payload["last_result"]["movement"]
    assert movement["requested_waypoints"] == [[6, 5], [8, 5]]
    assert movement["requested_final_world_cell"] == {"x": 12, "y": 5}
    assert movement["completed_waypoints"] == 1
    assert movement["final_target_attempted"] is False
    assert movement["route_results"][-1]["stop_reason"] == "movement_blocked"


def test_result_interpreter_runs_for_routine_results_and_preserves_public_fields() -> None:
    class FakeSummarizer:
        stream_output = False
        last_saved_memory_keys: list[str] = []

        def summarize(self, payload):
            assert payload["state"]["map"] == "Oak's Lab"
            return {
                "screen_description": "오박사 연구실에서 대화가 끝난 화면",
                "current_location": "Oak's Lab (5, 6)",
                "thought_summary": "대화는 끝났지만 스타터 획득 여부는 추가 확인이 필요합니다.",
                "summary": "dialog closed",
                "memory_saved": False,
            }

    result = ResultInterpreterAgent(summarizer=FakeSummarizer()).interpret(
        {
            "step_count": 3,
            "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter Pokemon"},
            "observation": observation(dialog_open=False),
            "active_action_plan": action_plan(),
            "action_outcome": {
                "status": "single_action_complete",
                "action_result": "success",
                "important_event": False,
                "state_changes": ["dialog_closed"],
            },
            "state_diff": {},
        }
    )

    interpretation = result["interpretation"]
    assert interpretation["llm_called"] is True
    assert interpretation["screen_description"] == "오박사 연구실에서 대화가 끝난 화면"
    assert interpretation["current_location"] == "Oak's Lab (5, 6)"
    assert interpretation["thought_summary"] == (
        "대화는 끝났지만 스타터 획득 여부는 추가 확인이 필요합니다."
    )
    assert result["goal"] == {
        "main": "Complete Pokemon Red",
        "sub": "Choose a starter Pokemon",
    }


def test_valid_goal_tool_result_survives_invalid_interpreter_json() -> None:
    class FailedSummarizer:
        stream_output = False
        last_saved_memory_keys: list[str] = []
        last_interpret_error = "invalid_json_response"
        last_goal_update = {
            "phase": "goal_update",
            "changed": True,
            "goal": {
                "main": "Complete Pokemon Red",
                "sub": "Reach Viridian City",
            },
        }

        def summarize(self, payload):
            return None

    result = ResultInterpreterAgent(summarizer=FailedSummarizer()).interpret(
        {
            "step_count": 4,
            "goal": {"main": "Complete Pokemon Red", "sub": "Leave Oak's Lab"},
            "observation": observation(dialog_open=False),
            "active_action_plan": action_plan(),
            "action_outcome": {"status": "single_action_complete"},
            "state_diff": {},
        }
    )

    assert result["goal"] == {
        "main": "Complete Pokemon Red",
        "sub": "Reach Viridian City",
    }
    assert result["interpret_error"] == "invalid_json_response"
    assert result["interpretation"]["goal_updated"] is True
