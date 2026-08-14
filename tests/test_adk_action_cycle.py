from __future__ import annotations

from pokemon_agent.adk_agent.coordinator.action_cycle import (
    action_cycle_needs_planning,
    build_state_diff,
    evaluate_state_condition,
    normalize_repeat_condition,
    verify_action_cycle,
    verify_goal,
)
from pokemon_agent.adk_agent.agents.interpreter.agent import compact_interpreter_context


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


def action_plan(*, repeat_count: int = 0, max_repeats: int = 3) -> dict:
    return {
        "action": {"type": "buttons", "buttons": ["a", "wait"], "reason": "advance_dialog"},
        "repeat_until": {"path": "dialog_open", "equals": False},
        "repeat_count": repeat_count,
        "max_repeats": max_repeats,
        "status": "active",
    }


def test_repeat_condition_contract_is_strict() -> None:
    assert normalize_repeat_condition({"path": "dialog_open", "equals": False}) == {
        "path": "dialog_open",
        "equals": False,
    }
    assert normalize_repeat_condition({"path": "dialog_open", "not_equals": True}) is None
    assert normalize_repeat_condition({"equals": False}) is None


def test_state_condition_reads_nested_ram_state() -> None:
    assert evaluate_state_condition(
        {"path": "position", "equals": {"x": 5, "y": 6}},
        observation(),
    )["matched"] is True
    assert evaluate_state_condition(
        {"path": "flags.got_starter", "equals": True},
        observation(),
    )["matched"] is False


def test_action_cycle_continues_until_repeat_condition_is_met() -> None:
    before = observation(dialog_open=True)
    unchanged_diff = build_state_diff(before, before)
    updated, outcome = verify_action_cycle(
        action_plan(),
        after_observation=before,
        action_result={"stop_reason": "buttons_complete"},
        state_diff=unchanged_diff,
        goal_completed=False,
    )

    assert updated["status"] == "active"
    assert updated["repeat_count"] == 1
    assert outcome["status"] == "continue"

    after = observation(dialog_open=False)
    changed_diff = build_state_diff(before, after)
    updated, outcome = verify_action_cycle(
        updated,
        after_observation=after,
        action_result={"stop_reason": "buttons_complete"},
        state_diff=changed_diff,
        goal_completed=False,
    )

    assert updated["status"] == "condition_met"
    assert outcome["status"] == "condition_met"
    assert outcome["condition_result"]["matched"] is True


def test_action_cycle_stops_at_repeat_limit_or_execution_interruption() -> None:
    current = observation(dialog_open=True)
    state_diff = build_state_diff(current, current)
    _, exhausted = verify_action_cycle(
        action_plan(repeat_count=2, max_repeats=3),
        after_observation=current,
        action_result={"stop_reason": "buttons_complete"},
        state_diff=state_diff,
        goal_completed=False,
    )
    _, interrupted = verify_action_cycle(
        action_plan(),
        after_observation=current,
        action_result={"stop_reason": "realtime_ticker_stopped"},
        state_diff=state_diff,
        goal_completed=False,
    )

    assert exhausted["status"] == "max_repeats_reached"
    assert interrupted["status"] == "interrupted"


def test_action_cycle_replans_only_at_a_boundary() -> None:
    state = {
        "replan_required": False,
        "active_action_plan": action_plan(),
        "observation": observation(dialog_open=True),
    }
    assert action_cycle_needs_planning(state) is False

    state["observation"] = observation(dialog_open=False)
    assert action_cycle_needs_planning(state) is True


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
                **action_plan(repeat_count=3),
                "status": "condition_met",
            },
            "action_outcome": {
                "status": "condition_met",
                "reason": "repeat_condition_met",
                "repeat_count": 3,
                "max_repeats": 3,
                "goal_completed": False,
            },
            "state_diff": build_state_diff(before, after),
        }
    )

    assert payload["action_plan"]["action"]["type"] == "buttons"
    assert payload["last_result"]["status"] == "condition_met"
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
