from __future__ import annotations

import json

import pytest

from pokemon_agent.adk_agent.agents.goal import (
    DEFAULT_MAIN_GOAL,
    GoalUpdateBuffer,
    MAX_GOAL_TEXT_LENGTH,
    goal_from_main,
    normalize_goal,
)
from pokemon_agent.adk_agent.agents.interpreter.agent import ResultInterpreterAgent
from pokemon_agent.adk_agent.agents.executor.schema import compact_plan_decision
from pokemon_agent.adk_agent.agents.planner.schema import compact_state_for_prompt
from pokemon_agent.adk_agent.coordinator.loop import PokemonAdkLoop
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState


def test_goal_normalization_has_exact_main_sub_shape() -> None:
    assert normalize_goal(None) == {"main": DEFAULT_MAIN_GOAL, "sub": ""}
    assert normalize_goal({"main": "  Reach   Pewter  ", "sub": "  Leave Viridian  "}) == {
        "main": "Reach Pewter",
        "sub": "Leave Viridian",
    }
    assert set(goal_from_main("Finish the game")) == {"main", "sub"}


def test_goal_update_replaces_once_and_ignores_duplicate_calls() -> None:
    buffer = GoalUpdateBuffer()
    buffer.begin({"main": "Complete Pokemon Red", "sub": "Choose a starter"})

    first = buffer.update("Complete Pokemon Red", "Reach Viridian City")
    duplicate = buffer.update("Different main", "Different sub")

    assert first == {
        "phase": "goal_update",
        "changed": True,
        "goal": {"main": "Complete Pokemon Red", "sub": "Reach Viridian City"},
    }
    assert duplicate["duplicate_ignored"] is True
    assert duplicate["goal"] == first["goal"]
    assert buffer.last_update == first

    buffer.begin(first["goal"])
    unchanged = buffer.update(first["goal"]["main"], first["goal"]["sub"])
    assert unchanged["changed"] is False


def test_goal_update_validates_main_and_bounds_text() -> None:
    buffer = GoalUpdateBuffer()
    buffer.begin(None)

    with pytest.raises(ValueError, match="main goal"):
        buffer.update("   ", "")

    result = buffer.update("M" * 600, "")
    assert len(result["goal"]["main"]) == MAX_GOAL_TEXT_LENGTH
    assert result["goal"]["sub"] == ""


def test_loop_restores_only_latest_runtime_goal(tmp_path) -> None:
    store = FileAgentRuntimeState(tmp_path / "runtime.json")
    store.publish(
        {
            "goal": {
                "main": "Finish Pokemon Red",
                "sub": "Reach Pewter City",
                "history": ["legacy"],
            },
            "objective": "legacy objective",
            "current_goal": {"description": "legacy goal"},
        },
        phase="completed",
    )

    state = PokemonAdkLoop(object(), runtime_state_store=store).initialize_state(max_steps=5)

    assert state["goal"] == {"main": "Finish Pokemon Red", "sub": "Reach Pewter City"}
    assert "objective" not in state
    assert "current_goal" not in state
    persisted = store.read()
    assert persisted["goal"] == {"main": "Finish Pokemon Red", "sub": "Reach Pewter City"}
    assert "objective" not in persisted
    assert "current_goal" not in persisted


def test_missing_runtime_file_exposes_the_default_goal(tmp_path) -> None:
    store = FileAgentRuntimeState(tmp_path / "missing.json")

    assert store.read()["goal"] == {"main": DEFAULT_MAIN_GOAL, "sub": ""}


def test_explicit_main_goal_overrides_restored_goal_and_clears_sub(tmp_path) -> None:
    store = FileAgentRuntimeState(tmp_path / "runtime.json")
    store.publish(
        {"goal": {"main": "Old main", "sub": "Old sub"}},
        phase="completed",
    )

    state = PokemonAdkLoop(object(), runtime_state_store=store).initialize_state(
        main_goal="Catch Mewtwo",
        max_steps=5,
    )

    assert state["goal"] == {"main": "Catch Mewtwo", "sub": ""}


def test_legacy_runtime_goal_fields_are_ignored(tmp_path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "objective": "legacy objective",
                "current_goal": {"description": "legacy goal"},
            }
        ),
        encoding="utf-8",
    )

    state = PokemonAdkLoop(
        object(),
        runtime_state_store=FileAgentRuntimeState(path),
    ).initialize_state(max_steps=5)

    assert state["goal"] == {"main": DEFAULT_MAIN_GOAL, "sub": ""}


class _InterpreterResult:
    def __init__(self, goal_update=None, *, fail: bool = False) -> None:
        self.last_goal_update = goal_update
        self.last_saved_memory_keys = []
        self.fail = fail

    def summarize(self, _payload):
        if self.fail:
            raise RuntimeError("interpreter failed")
        return {"summary": "verified"}


def _interpreter_state() -> dict:
    return {
        "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter"},
        "step_count": 1,
        "mode": "overworld",
        "observation": {"state": {}},
        "active_action_plan": {"action": {"type": "buttons", "buttons": ["a"]}},
        "action_outcome": {
            "status": "single_action_complete",
            "action_result": "success",
            "reason": "buttons_complete",
            "state_changes": [],
        },
    }


def test_interpreter_goal_update_is_visible_to_the_next_planner_input() -> None:
    summarizer = _InterpreterResult(
        {
            "phase": "goal_update",
            "changed": True,
            "goal": {"main": "Complete Pokemon Red", "sub": "Reach Viridian City"},
        }
    )
    state = _interpreter_state()
    state.update(ResultInterpreterAgent(summarizer=summarizer).interpret(state))

    planner_context = compact_state_for_prompt(state)

    assert planner_context["goal"] == {
        "main": "Complete Pokemon Red",
        "sub": "Reach Viridian City",
    }


def test_action_history_plan_copy_does_not_store_goal_snapshots() -> None:
    compact = compact_plan_decision(
        {
            "agent": "pokemon_red_planning_agent",
            "phase": "planning",
            "goal": {"main": "Complete Pokemon Red", "sub": "Choose a starter"},
            "action_plan": {"action": {"type": "buttons", "buttons": ["a"]}},
        }
    )

    assert "goal" not in compact


@pytest.mark.parametrize("fail", [False, True])
def test_interpreter_preserves_goal_without_a_successful_goal_tool_call(fail: bool) -> None:
    state = _interpreter_state()
    state.update(ResultInterpreterAgent(summarizer=_InterpreterResult(fail=fail)).interpret(state))

    assert state["goal"] == {
        "main": "Complete Pokemon Red",
        "sub": "Choose a starter",
    }
