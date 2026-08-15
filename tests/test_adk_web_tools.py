from __future__ import annotations

from pathlib import Path

from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.agents.interpreter.prompt import RESULT_INTERPRETER_PROMPT
from pokemon_agent.adk_agent.agents.shared import MAX_AUTOMATIC_FUNCTION_CALLS
from pokemon_agent.input_contract import (
    BUTTON_TOKENS,
    MAX_BUTTONS_PER_ACTION,
    MAX_MOVE_PATH_STEPS,
    MAX_MOVE_WAYPOINTS,
    MAX_WORLD_NAVIGATION_SEGMENTS,
)
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.adk_agent.agent import app as entrypoint_app
from pokemon_agent.adk_agent.agent import root_agent as entrypoint_root_agent
from pokemon_agent.adk_agent.web import tools as web_tools
from pokemon_agent.adk_agent.web.app import (
    _McpDashboardHub,
    _compact_dashboard_runtime_state,
    app,
    build_app,
    build_root_agent,
)
from pokemon_agent.adk_agent.web.prompt import WEB_AGENT_PROMPT
import pokemon_agent.adk_agent.runtime.state as runtime_state_module


def test_adk_dev_ui_entrypoint_exports_the_compacted_team_app() -> None:
    assert entrypoint_app.name == "adk_agent"
    assert entrypoint_app.root_agent is entrypoint_root_agent
    assert entrypoint_root_agent.name == "pokemon_red_web_coordinator"
    assert entrypoint_root_agent.sub_agents[0].name == "pokemon_red_team"


def test_adk_web_tools_read_shared_cli_runtime_history(tmp_path: Path, monkeypatch) -> None:
    store = FileAgentRuntimeState(tmp_path / "adk_runtime_state.json")
    store.publish(
        {
            "objective": "finish Pokemon Red",
            "step_count": 7,
            "mode": "overworld",
            "action_history": [{"step": 6}, {"step": 7}],
            "history_summary": "Started in Oak's Lab.",
        },
        phase="executed",
    )
    monkeypatch.setattr(web_tools, "_runtime_store", lambda: store)

    status = web_tools.agent_runtime_status()
    actions = web_tools.recent_agent_actions(limit=1)

    assert status["phase"] == "executed"
    assert status["step_count"] == 7
    assert actions["actions"] == [{"step": 7}]


def test_adk_web_dashboard_bridge_forwards_compact_agent_updates() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def publish_dashboard_trace(self, trace):
            self.calls.append(("trace", trace))

        def publish_dashboard_runtime(self, state, *, phase):
            self.calls.append(("runtime", state, phase))

        def publish_dashboard_memory(self, items, activity=None):
            self.calls.append(("memory", items, activity))

    client = FakeClient()
    hub = _McpDashboardHub(client)
    state = {
        "step_count": 3,
        "max_steps": 100,
        "planned_action": {"type": "buttons", "buttons": ["a"]},
        "action_result": {
            "stop_reason": "buttons_complete",
            "before_observation": {"screenshot": {"base64": "large"}},
            "after_observation": {"screenshot": {"base64": "large"}},
        },
        "observation": {"screenshot": {"base64": "large"}},
    }

    hub.publish_trace({"phase": "planning_thinking", "thinking_summary": "Read the dialog."})
    hub.publish_runtime(state, phase="planned")
    hub.publish_memory_snapshot({"map:Oak's Lab": {"value": "starter table"}})
    hub.publish_memory_activity(
        {"map:Oak's Lab": {"value": "starter table"}},
        {"tool": "search_memory", "keys": ["map:Oak's Lab"]},
    )

    runtime = client.calls[1][1]
    assert runtime["step_count"] == 3
    assert runtime["action_result"] == {"stop_reason": "buttons_complete"}
    assert "observation" not in runtime
    assert client.calls[0][1]["thinking_summary"] == "Read the dialog."
    assert client.calls[-1][2]["keys"] == ["map:Oak's Lab"]
    assert _compact_dashboard_runtime_state(state) == runtime


def test_recent_agent_actions_is_capped_at_twenty_turns(tmp_path: Path, monkeypatch) -> None:
    store = FileAgentRuntimeState(tmp_path / "adk_runtime_state.json")
    store.publish(
        {"action_history": [{"step": step} for step in range(22)]},
        phase="executed",
    )
    monkeypatch.setattr(web_tools, "_runtime_store", lambda: store)

    actions = web_tools.recent_agent_actions(limit=20)

    assert actions["count"] == 20
    assert actions["actions"][0] == {"step": 2}


def test_runtime_state_publish_retries_windows_replace_contention(tmp_path: Path, monkeypatch) -> None:
    store = FileAgentRuntimeState(tmp_path / "adk_runtime_state.json")
    real_replace = runtime_state_module.os.replace
    calls = 0

    def flaky_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("temporarily locked by reader")
        return real_replace(source, destination)

    monkeypatch.setattr(runtime_state_module.os, "replace", flaky_replace)
    store.publish({"step_count": 1, "termination_reason": None}, phase="observed")

    assert calls == 2
    assert store.read()["step_count"] == 1


def test_adk_web_root_agent_exposes_traceable_runtime_team() -> None:
    root_agent = build_root_agent(model="gemini-3.5-flash")

    assert root_agent.name == "pokemon_red_web_coordinator"
    assert [agent.name for agent in root_agent.sub_agents] == [
        "pokemon_red_team",
    ]
    team = root_agent.sub_agents[0]
    assert [agent.name for agent in team.sub_agents] == [
        "pokemon_red_planning_agent",
        "pokemon_red_execution_agent",
        "pokemon_red_result_interpreter_agent",
    ]
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in root_agent.tools}
    assert tool_names == {"agent_runtime_status", "recent_agent_actions"}


def test_adk_web_prompt_routes_bounded_play_requests_to_runtime_team() -> None:
    assert "in any language" in WEB_AGENT_PROMPT
    assert 'transfer_to_agent(agent_name="pokemon_red_team")' in WEB_AGENT_PROMPT
    assert "exactly once" in WEB_AGENT_PROMPT
    assert "same ADK invocation" in WEB_AGENT_PROMPT
    assert "separate SDL2/Qt MCP game worker" in WEB_AGENT_PROMPT
    assert "do not start the external CLI runner" in WEB_AGENT_PROMPT


def test_adk_web_exports_compacted_app() -> None:
    configured = build_app(model="gemini-3.5-flash")

    assert app.root_agent is not None
    assert configured.name == "adk_agent"
    assert configured.root_agent.name == "pokemon_red_web_coordinator"
    assert configured.events_compaction_config.compaction_interval == 5
    assert configured.events_compaction_config.overlap_size == 1
    assert configured.events_compaction_config.token_threshold is not None
    assert configured.events_compaction_config.event_retention_size == 8
    assert (
        configured.root_agent.generate_content_config.automatic_function_calling.maximum_remote_calls
        == MAX_AUTOMATIC_FUNCTION_CALLS
    )


def test_adk_web_planning_prompt_documents_direct_action_contract() -> None:
    assert '{"type":"buttons","buttons"' in PLANNING_AGENT_PROMPT
    assert '["right","wait","right","wait","a"]' in PLANNING_AGENT_PROMPT
    assert '["right","wait","right","wait","right"]' in PLANNING_AGENT_PROMPT
    assert "same button token may appear multiple times" in PLANNING_AGENT_PROMPT
    assert "executor runs it exactly once" in PLANNING_AGENT_PROMPT
    assert "separate repetition-control fields" in PLANNING_AGENT_PROMPT
    assert "world coordinates" in PLANNING_AGENT_PROMPT
    assert "walk_area_collision coordinates" not in PLANNING_AGENT_PROMPT
    assert '"screen_description"' in PLANNING_AGENT_PROMPT
    assert '"current_location"' in PLANNING_AGENT_PROMPT
    assert '"thought_summary"' in PLANNING_AGENT_PROMPT
    assert "one detailed paragraph each" in PLANNING_AGENT_PROMPT
    assert "using 3-5 complete sentences" in PLANNING_AGENT_PROMPT
    assert "decision_trace" not in PLANNING_AGENT_PROMPT
    assert "session_dialog" not in PLANNING_AGENT_PROMPT
    assert "RAM/GameState" in PLANNING_AGENT_PROMPT
    assert "repeat_until" not in PLANNING_AGENT_PROMPT
    assert "max_repeats" not in PLANNING_AGENT_PROMPT
    assert 'search_memory(queries=[{"memory_type":"map","name":state.map_name}, ...])' in PLANNING_AGENT_PROMPT
    assert "Never call `search_memory` a second time" in PLANNING_AGENT_PROMPT
    assert "save_memory(entries=" in RESULT_INTERPRETER_PROMPT
    for memory_type in ("map", "npc", "pokemon", "event"):
        assert f"`{memory_type}`" in PLANNING_AGENT_PROMPT
    assert "<memory_type>:<name>" in PLANNING_AGENT_PROMPT
    assert "Professor Oak" in RESULT_INTERPRETER_PROMPT
    assert "starter_selection" in RESULT_INTERPRETER_PROMPT
    assert "verified world-coordinate routes" in PLANNING_AGENT_PROMPT
    assert "navigation.reachable_targets" in PLANNING_AGENT_PROMPT
    assert '"waypoints":[[12,8],[18,8]]' in PLANNING_AGENT_PROMPT
    assert f"1..{MAX_MOVE_WAYPOINTS}" in PLANNING_AGENT_PROMPT
    assert "visits each waypoint in array order" in PLANNING_AGENT_PROMPT
    assert "remaining waypoints and the final target are not attempted" in PLANNING_AGENT_PROMPT
    assert "state.controls_locked" in PLANNING_AGENT_PROMPT
    assert "wait_for_scripted_transition" in PLANNING_AGENT_PROMPT
    assert "Dialog understanding policy" in PLANNING_AGENT_PROMPT
    assert "Which POKEMON do you want?" in PLANNING_AGENT_PROMPT
    assert "last_dialog" in PLANNING_AGENT_PROMPT
    assert "approach_bulbasaur_poke_ball" in PLANNING_AGENT_PROMPT
    assert "Starter selection is a multi-observation workflow" in PLANNING_AGENT_PROMPT
    assert "select the visible YES option" in PLANNING_AGENT_PROMPT
    assert '"reason":"advance_dialog"' not in PLANNING_AGENT_PROMPT
    assert "starter-ball confirmation names the candidate species" in RESULT_INTERPRETER_PROMPT
    assert "route: [from_x,from_y] -> [to_x,to_y]" in RESULT_INTERPRETER_PROMPT
    assert "Merge and deduplicate coordinates" in RESULT_INTERPRETER_PROMPT
    assert "zero-step move adds no route knowledge" in RESULT_INTERPRETER_PROMPT
    for field in ("screen_description", "current_location", "thought_summary"):
        assert f'"{field}"' in RESULT_INTERPRETER_PROMPT


def test_planner_and_interpreter_prompts_share_complete_input_and_navigation_contract() -> None:
    token_list = ", ".join(BUTTON_TOKENS)
    for prompt in (PLANNING_AGENT_PROMPT, RESULT_INTERPRETER_PROMPT):
        assert token_list in prompt
        assert f"1..{MAX_BUTTONS_PER_ACTION}" in prompt
        assert str(MAX_MOVE_PATH_STEPS) in prompt
        assert str(MAX_MOVE_WAYPOINTS) in prompt
        assert "Dijkstra" in prompt
        assert "current-map world coordinate" in prompt

    assert "navigation.reachable_targets" in PLANNING_AGENT_PROMPT
    assert "persistent current-map destination" in PLANNING_AGENT_PROMPT
    assert "even when it is off-screen" in PLANNING_AGENT_PROMPT
    assert str(MAX_WORLD_NAVIGATION_SEGMENTS) in PLANNING_AGENT_PROMPT
    assert "ordered by path length from short to long" in PLANNING_AGENT_PROMPT
    assert "use the farthest verified coordinate" in PLANNING_AGENT_PROMPT
    assert "leaving the room" in PLANNING_AGENT_PROMPT
    assert "Do not target a screen boundary" in PLANNING_AGENT_PROMPT
    assert "A map transition ends the move" in PLANNING_AGENT_PROMPT
    assert "Never output prose labels" in PLANNING_AGENT_PROMPT
    assert "Do not use Markdown fences" in PLANNING_AGENT_PROMPT
    assert "one detailed paragraph each" in PLANNING_AGENT_PROMPT
    assert "one detailed paragraph each" in RESULT_INTERPRETER_PROMPT
    assert not any("\uac00" <= char <= "\ud7a3" for char in PLANNING_AGENT_PROMPT)
    assert not any("\uac00" <= char <= "\ud7a3" for char in RESULT_INTERPRETER_PROMPT)
    assert "navigation_limit_reached" in RESULT_INTERPRETER_PROMPT
    assert "interrupted_map_change" in RESULT_INTERPRETER_PROMPT
    assert "Do not emit an action object" in RESULT_INTERPRETER_PROMPT
