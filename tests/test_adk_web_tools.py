from __future__ import annotations

from pathlib import Path

from pokemon_agent import mcp_server
from pokemon_agent.adk_agent.agents.planner.prompt import PLANNING_AGENT_PROMPT
from pokemon_agent.adk_agent.agents.interpreter.prompt import RESULT_INTERPRETER_PROMPT
from pokemon_agent.adk_agent.agents.shared import MAX_AUTOMATIC_FUNCTION_CALLS
from pokemon_agent.input_contract import (
    BUTTON_TOKENS,
    MAX_BUTTONS_PER_ACTION,
    MAX_MOVE_PATH_STEPS,
    MAX_WORLD_NAVIGATION_SEGMENTS,
)
from pokemon_agent.adk_agent.runtime.state import FileAgentRuntimeState
from pokemon_agent.adk_agent.web import tools as web_tools
from pokemon_agent.adk_agent.web.app import app, build_app, build_root_agent
import pokemon_agent.adk_agent.runtime.state as runtime_state_module
from pokemon_agent.memory.file_memory import FileLongTermMemory
import pokemon_agent.session as session_module
from pokemon_agent.session import PokemonSession

from tests.fakes import FakePokemonEnvironment, fake_session_paths


def test_adk_web_tools_observe_returns_compact_screenshot(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    observation = web_tools.observe_game(include_screenshot_base64=True)

    assert observation["screenshot"]["format"] == "png"
    assert observation["screenshot"]["width"] == 160
    assert observation["screenshot"]["height"] == 144
    assert observation["screenshot"]["base64"]
    assert observation["screenshot_overlay"]["format"] == "png"
    assert observation["screenshot_overlay"]["width"] == 640
    assert observation["screenshot_overlay"]["height"] == 576
    assert observation["screenshot_overlay"]["collision_truthy"] == "walkable"
    assert observation["screenshot_overlay"]["walk_cell_size"] == 2
    assert observation["screenshot_overlay"]["player_map_position"] == {"x": 5, "y": 6}
    assert "walk_cell_map_coordinates" in observation["screenshot_overlay"]["overlays"]
    assert "screen_tile_coordinates" not in observation["screenshot_overlay"]["overlays"]
    assert observation["screenshot_overlay"]["base64"]
    assert len(observation["visible_world_cells"]) == 9
    assert len(observation["visible_world_cells"][0]) == 10
    assert observation["visible_world_cells"][4][4] == {"x": 5, "y": 6, "walkable": True}
    assert {"direction": "right", "x": 6, "y": 6} in observation["safe_neighbor_world_cells"]
    assert observation["state_events"][0]["type"] == "initial_observation"
    assert observation["state"]["dialog"]["open"] is False
    assert "battle" not in observation["state"]
    assert observation["state"]["counts"]["party"] == 0
    assert "raw" not in observation["state"]


def test_adk_web_tools_move_and_command_log(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    result = web_tools.move([6, 6])
    log = web_tools.recent_game_commands(limit=20)

    assert result["executed_actions"][0]["button"] == "right"
    assert result["after_observation"]["screenshot"]["base64_length"] > 0
    assert result["after_observation"]["screenshot_overlay"]["base64_length"] > 0
    assert "move_to_world_cell" in [entry["tool"] for entry in log["commands"]]


def test_adk_web_tools_action_contract_wrappers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    pressed = web_tools.buttons(["a", "wait"])
    moved = web_tools.move([6, 6])
    log = web_tools.recent_game_commands(limit=20)

    assert [action["button"] for action in pressed["executed_actions"]] == ["a", "wait"]
    assert moved["requested_world_cell"] == {"x": 6, "y": 6}
    assert "requested_walk_cell" not in moved
    move_entries = [entry for entry in log["commands"] if entry["tool"] == "move_to_world_cell"]
    assert move_entries[-1]["args"] == {"target_x": 6, "target_y": 6}


def test_adk_web_tools_press_buttons_and_wait(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "ACTION_WAIT_SECONDS", 0.01)
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    pressed = web_tools.buttons(["a"])
    waited = web_tools.wait()
    log = web_tools.recent_game_commands(limit=20)

    assert pressed["executed_actions"][0]["button"] == "a"
    assert waited["waited"] is True
    assert "press_buttons" in [entry["tool"] for entry in log["commands"]]
    assert "wait" in [entry["tool"] for entry in log["commands"]]


def test_adk_web_tools_save_screenshot_uses_date_folder(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    result = web_tools.save_current_screenshot()
    saved_path = Path(result["path"])

    assert result["saved"] is True
    assert saved_path.exists()
    assert saved_path.parent.parent == tmp_path / "captures"
    assert saved_path.parent.name.isdigit()
    assert saved_path.name.startswith("adk_web_")
    assert saved_path.suffix == ".png"


def test_adk_web_memory_tools_support_validated_memory_types(tmp_path: Path, monkeypatch) -> None:
    store = FileLongTermMemory(tmp_path / "long_term_memory.json")
    monkeypatch.setattr(web_tools, "_memory_store", lambda: store)

    write_result = web_tools.save_memory("event", "starter_selection", "Bulbasaur was chosen")
    search_result = web_tools.search_memory("event", "starter_selection")

    assert write_result["phase"] == "memory_write"
    assert write_result["key"] == "event:starter_selection"
    assert search_result["key"] == "event:starter_selection"
    assert search_result["item"]["value"] == "Bulbasaur was chosen"
    assert list(store.items()) == ["event:starter_selection"]


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


def test_adk_web_root_agent_exposes_only_llm_decision_sub_agents() -> None:
    root_agent = build_root_agent(model="gemini-3.5-flash")

    assert root_agent.name == "pokemon_red_team"
    assert [agent.name for agent in root_agent.sub_agents] == [
        "pokemon_red_planning_agent",
        "pokemon_red_result_interpreter_agent",
    ]
    planner_tools = {
        getattr(tool, "name", getattr(tool, "__name__", ""))
        for tool in root_agent.sub_agents[0].tools
    }
    interpreter_tools = {
        getattr(tool, "name", getattr(tool, "__name__", ""))
        for tool in root_agent.sub_agents[1].tools
    }
    assert "search_memory" in planner_tools
    assert {"search_memory", "save_memory"} <= interpreter_tools
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in root_agent.tools}
    assert {"agent_runtime_status", "recent_agent_actions", "buttons", "move", "wait"} <= tool_names
    assert "run_rule_based_step" not in tool_names
    assert "run_team_step" not in tool_names


def test_adk_web_exports_compacted_app() -> None:
    configured = build_app(model="gemini-3.5-flash")

    assert app.root_agent is not None
    assert configured.name == "adk_agent"
    assert configured.root_agent.name == "pokemon_red_team"
    assert configured.events_compaction_config.compaction_interval == 5
    assert configured.events_compaction_config.overlap_size == 1
    assert configured.events_compaction_config.token_threshold is not None
    assert configured.events_compaction_config.event_retention_size == 20
    for agent in (configured.root_agent, *configured.root_agent.sub_agents):
        assert (
            agent.generate_content_config.automatic_function_calling.maximum_remote_calls
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
    assert 'search_memory(memory_type="map", name=state.map_name)' in PLANNING_AGENT_PROMPT
    for memory_type in ("map", "npc", "pokemon", "event"):
        assert f"`{memory_type}`" in PLANNING_AGENT_PROMPT
    assert "<memory_type>:<name>" in PLANNING_AGENT_PROMPT
    assert "Professor Oak" in RESULT_INTERPRETER_PROMPT
    assert "starter_selection" in RESULT_INTERPRETER_PROMPT
    assert "verified world-coordinate routes" in PLANNING_AGENT_PROMPT
    assert "navigation.reachable_targets" in PLANNING_AGENT_PROMPT
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
