from __future__ import annotations

from pathlib import Path

from pokemon_agent import mcp_server
from pokemon_agent.adk_agent import web_tools
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
    assert "raw" not in observation["state"]


def test_adk_web_tools_move_and_command_log(tmp_path: Path) -> None:
    fake_env = FakePokemonEnvironment()
    session = PokemonSession(
        paths=fake_session_paths(tmp_path),
        env_factory=lambda rom, window: fake_env,
    )
    mcp_server.set_session_for_tests(session)

    web_tools.start_game(load_fixed=False, control_ui=False, realtime_ticks=False)
    result = web_tools.move_to_screen_tile(10, 8, max_steps=1)
    log = web_tools.recent_game_commands(limit=20)

    assert result["executed_actions"][0]["button"] == "right"
    assert result["after_observation"]["screenshot"]["base64_length"] > 0
    assert "move_to_screen_tile" in [entry["tool"] for entry in log["commands"]]


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
