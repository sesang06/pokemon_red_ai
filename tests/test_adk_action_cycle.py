from __future__ import annotations

from pokemon_agent.adk_agent.coordinator.action_cycle import (
    build_state_diff,
    evaluate_state_condition,
    goal_from_objective,
    verify_action_cycle,
    verify_goal,
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


def test_state_condition_reads_nested_ram_state() -> None:
    assert evaluate_state_condition(
        {"path": "position", "equals": {"x": 5, "y": 6}},
        observation(),
    )["matched"] is True
    assert evaluate_state_condition(
        {"path": "flags.got_starter", "equals": True},
        observation(),
    )["matched"] is False
    assert evaluate_state_condition({"path": "dialog_open", "not_equals": True}, observation())["matched"] is None


def test_objectives_do_not_receive_hardcoded_ram_success_conditions() -> None:
    for objective in ("obtain_pokeballs", "obtain_first_pokemon", "reach_viridian_city"):
        goal = goal_from_objective(objective)
        assert goal["id"] == objective
        assert goal["description"] == objective
        assert goal["success_conditions"] == []


def test_action_cycle_completes_after_exactly_one_execution() -> None:
    before = observation(dialog_open=True)
    after = observation(dialog_open=False)
    updated, outcome = verify_action_cycle(
        action_plan(),
        action_result={"stop_reason": "buttons_complete"},
        state_diff=build_state_diff(before, after),
        goal_completed=False,
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
        goal_completed=False,
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
            goal_completed=False,
        )

        assert outcome["status"] == "single_action_complete"
        assert outcome["action_result"] == "success"
        assert outcome["reason"] == stop_reason


def test_goal_verification_remains_deterministic() -> None:
    goal = {"success_conditions": [{"path": "flags.got_starter", "equals": True}]}
    assert verify_goal(goal, observation())["verified"] is False

    completed = observation()
    completed["state"]["flags"]["got_starter"] = True
    assert verify_goal(goal, completed)["verified"] is True


def test_interpreter_context_contains_action_outcome_without_task_fields() -> None:
    before = observation(dialog_open=True)
    after = observation(dialog_open=False)
    payload = compact_interpreter_context(
        {
            "step_count": 3,
            "observation": after,
            "active_action_plan": {
                **action_plan(),
                "status": "single_action_complete",
            },
            "action_outcome": {
                "status": "single_action_complete",
                "reason": "single_action_complete",
                "goal_completed": False,
            },
            "state_diff": build_state_diff(before, after),
        }
    )

    assert payload["action_plan"]["action"]["type"] == "buttons"
    assert payload["last_result"]["status"] == "single_action_complete"
    assert "repeat" not in str(payload)
    assert "task" not in payload
    assert "failed_preconditions" not in str(payload)


def test_interpreter_context_keeps_compact_bounded_movement_result() -> None:
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
                "stop_reason": "max_steps_reached",
                "result": {
                    "requested_world_cell": {"x": 20, "y": 5},
                    "resolved_world_cell": {"x": 13, "y": 5},
                    "target_out_of_visible_area": True,
                    "steps_taken": 8,
                    "stop_reason": "max_steps_reached",
                },
            },
        }
    )

    assert payload["last_result"]["movement"] == {
        "requested_target": [20, 5],
        "requested_world_cell": {"x": 20, "y": 5},
        "resolved_world_cell": {"x": 13, "y": 5},
        "target_out_of_visible_area": True,
        "steps_taken": 8,
        "stop_reason": "max_steps_reached",
    }


def test_result_interpreter_preserves_public_screen_location_and_summary_fields() -> None:
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
                "goal_progress": 0.5,
                "memory_saved": False,
            }

    result = ResultInterpreterAgent(summarizer=FakeSummarizer()).interpret(
        {
            "step_count": 3,
            "observation": observation(dialog_open=False),
            "active_action_plan": action_plan(),
            "action_outcome": {
                "status": "single_action_complete",
                "action_result": "success",
                "important_event": True,
                "goal_completed": False,
                "state_changes": ["dialog_closed"],
            },
            "state_diff": {},
        }
    )

    interpretation = result["interpretation"]
    assert interpretation["screen_description"] == "오박사 연구실에서 대화가 끝난 화면"
    assert interpretation["current_location"] == "Oak's Lab (5, 6)"
    assert interpretation["thought_summary"] == (
        "대화는 끝났지만 스타터 획득 여부는 추가 확인이 필요합니다."
    )
